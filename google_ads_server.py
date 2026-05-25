from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import Field
import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
import logging

# MCP
from mcp.server.fastmcp import FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('google_ads_server')

# In-process cache so repeated get_credentials() (e.g. CLI warmup + GAQL) does not re-log / re-read files.
_credentials_cache: Optional[Any] = None


def reset_credentials_cache() -> None:
    """Clear cached credentials (for tests or after switching auth env in the same process)."""
    global _credentials_cache
    _credentials_cache = None


def _fastmcp_listen_host_port() -> tuple[str, int]:
    """
    Host/port for SSE / streamable-http.

    The MCP Python SDK passes host and port explicitly into Settings(), so
    FASTMCP_HOST / FASTMCP_PORT env vars alone are ignored unless we pass them here.
    """
    if os.environ.get("PORT"):
        return ("0.0.0.0", int(os.environ["PORT"]))
    host = os.environ.get("FASTMCP_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("FASTMCP_PORT", "8000"))
    except ValueError:
        port = 8000
    return (host, port)


_fm_host, _fm_port = _fastmcp_listen_host_port()

mcp = FastMCP(
    "google-ads-server",
    dependencies=[
        "google-auth-oauthlib",
        "google-auth",
        "requests",
        "python-dotenv"
    ],
    host=_fm_host,
    port=_fm_port,
)

# Constants and configuration
SCOPES = ['https://www.googleapis.com/auth/adwords']
API_VERSION = "v24"  # Google Ads API version


def _google_ads_http_timeout_seconds() -> float:
    """Seconds for ``requests`` calls to the Google Ads API (GAQL search, etc.)."""
    raw = os.environ.get("GOOGLE_ADS_REQUEST_TIMEOUT", "120").strip()
    try:
        t = float(raw)
        return t if t > 0 else 120.0
    except ValueError:
        return 120.0


# Load environment variables
try:
    from dotenv import load_dotenv
    # Load from .env file if it exists
    load_dotenv()
    logger.info("Environment variables loaded from .env file")
except ImportError:
    logger.warning("python-dotenv not installed, skipping .env file loading")

# Get credentials from environment variables
GOOGLE_ADS_CREDENTIALS_PATH = os.environ.get("GOOGLE_ADS_CREDENTIALS_PATH")
GOOGLE_ADS_DEVELOPER_TOKEN = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
GOOGLE_ADS_LOGIN_CUSTOMER_ID = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")
GOOGLE_ADS_AUTH_TYPE = os.environ.get("GOOGLE_ADS_AUTH_TYPE", "oauth")  # oauth or service_account


def _load_credentials_dict_from_env() -> Optional[Dict[str, Any]]:
    """Parse GOOGLE_ADS_CREDENTIALS_JSON if set (e.g. Railway secret). Returns None if unset or empty."""
    raw = os.environ.get("GOOGLE_ADS_CREDENTIALS_JSON")
    if raw is None or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"GOOGLE_ADS_CREDENTIALS_JSON must be valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("GOOGLE_ADS_CREDENTIALS_JSON must be a JSON object")
    return data


def _oauth_user_token_path() -> str:
    """Where to persist OAuth user tokens (refresh). Uses PATH if set, else TMPDIR."""
    if GOOGLE_ADS_CREDENTIALS_PATH and str(GOOGLE_ADS_CREDENTIALS_PATH).strip():
        token_path = str(GOOGLE_ADS_CREDENTIALS_PATH).strip()
        if os.path.exists(token_path) and not os.path.basename(token_path).endswith(".json"):
            token_dir = os.path.dirname(token_path) or "."
            token_path = os.path.join(token_dir, "google_ads_token.json")
        return token_path
    base = os.environ.get("TMPDIR", "/tmp")
    return os.path.join(base, "google_ads_token.json")


def format_customer_id(customer_id: str) -> str:
    """Format customer ID to ensure it's 10 digits without dashes."""
    # Convert to string if passed as integer or another type
    customer_id = str(customer_id)
    
    # Remove any quotes surrounding the customer_id (both escaped and unescaped)
    customer_id = customer_id.replace('\"', '').replace('"', '')
    
    # Remove any non-digit characters (including dashes, braces, etc.)
    customer_id = ''.join(char for char in customer_id if char.isdigit())
    
    # Ensure it's 10 digits with leading zeros if needed
    return customer_id.zfill(10)


def get_credentials():
    """
    Get and refresh OAuth credentials or service account credentials based on the auth type.
    
    This function supports two authentication methods:
    1. OAuth 2.0 (User Authentication) - For individual users or desktop applications
    2. Service Account (Server-to-Server Authentication) - For automated systems

    Returns:
        Valid credentials object to use with Google Ads API
    
    Results are cached in-process while OAuth tokens remain valid (or for the lifetime of a
    loaded service account object) to avoid duplicate log noise on back-to-back calls.
    """
    global _credentials_cache

    if _credentials_cache is not None:
        c = _credentials_cache
        if isinstance(c, service_account.Credentials):
            return c
        if getattr(c, "valid", False):
            return c
        _credentials_cache = None

    if not (GOOGLE_ADS_CREDENTIALS_PATH and str(GOOGLE_ADS_CREDENTIALS_PATH).strip()) and _load_credentials_dict_from_env() is None:
        raise ValueError(
            "Set GOOGLE_ADS_CREDENTIALS_JSON or GOOGLE_ADS_CREDENTIALS_PATH to load Google Ads credentials"
        )
    
    auth_type = GOOGLE_ADS_AUTH_TYPE.lower()
    logger.info(f"Using authentication type: {auth_type}")
    
    # Service Account authentication
    if auth_type == "service_account":
        try:
            creds = get_service_account_credentials()
            _credentials_cache = creds
            return creds
        except Exception as e:
            logger.error(f"Error with service account authentication: {str(e)}")
            raise
    
    # OAuth 2.0 authentication (default)
    creds = get_oauth_credentials()
    _credentials_cache = creds
    return creds

def get_service_account_credentials():
    """Get credentials using a service account key from env JSON or key file."""
    creds_dict = _load_credentials_dict_from_env()
    if creds_dict is not None:
        logger.info("Loading service account credentials from GOOGLE_ADS_CREDENTIALS_JSON")
        if creds_dict.get("type") != "service_account":
            raise ValueError(
                'GOOGLE_ADS_CREDENTIALS_JSON must be a service account key ({"type": "service_account", ...})'
            )
        try:
            credentials = service_account.Credentials.from_service_account_info(
                creds_dict,
                scopes=SCOPES,
            )
        except Exception as e:
            logger.error(f"Error loading service account credentials from JSON: {str(e)}")
            raise
    else:
        logger.info(f"Loading service account credentials from {GOOGLE_ADS_CREDENTIALS_PATH}")
        if not GOOGLE_ADS_CREDENTIALS_PATH or not os.path.exists(GOOGLE_ADS_CREDENTIALS_PATH):
            raise FileNotFoundError(
                f"Service account key file not found at {GOOGLE_ADS_CREDENTIALS_PATH}"
            )
        try:
            credentials = service_account.Credentials.from_service_account_file(
                GOOGLE_ADS_CREDENTIALS_PATH,
                scopes=SCOPES,
            )
        except Exception as e:
            logger.error(f"Error loading service account credentials: {str(e)}")
            raise

    impersonation_email = os.environ.get("GOOGLE_ADS_IMPERSONATION_EMAIL")
    if impersonation_email:
        logger.info(f"Impersonating user: {impersonation_email}")
        credentials = credentials.with_subject(impersonation_email)

    return credentials


def get_oauth_credentials():
    """Get and refresh OAuth user credentials from env JSON and/or token file."""
    creds = None
    client_config = None
    creds_dict = _load_credentials_dict_from_env()

    if creds_dict is not None:
        if creds_dict.get("type") == "service_account":
            raise ValueError(
                "GOOGLE_ADS_CREDENTIALS_JSON is a service account key; "
                "set GOOGLE_ADS_AUTH_TYPE=service_account instead of oauth"
            )
        if "installed" in creds_dict or "web" in creds_dict:
            client_config = creds_dict
            logger.info("Found OAuth client configuration in GOOGLE_ADS_CREDENTIALS_JSON")
        else:
            logger.info("Loading OAuth user credentials from GOOGLE_ADS_CREDENTIALS_JSON")
            creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
        token_path = _oauth_user_token_path()
    else:
        # Path to store the refreshed token
        token_path = GOOGLE_ADS_CREDENTIALS_PATH
        if token_path and os.path.exists(token_path) and not os.path.basename(token_path).endswith(".json"):
            token_dir = os.path.dirname(token_path)
            token_path = os.path.join(token_dir, "google_ads_token.json")

        if token_path and os.path.exists(token_path):
            try:
                logger.info(f"Loading OAuth credentials from {token_path}")
                with open(token_path, "r") as f:
                    creds_data = json.load(f)
                    if "installed" in creds_data or "web" in creds_data:
                        client_config = creds_data
                        logger.info("Found OAuth client configuration")
                    else:
                        logger.info("Found existing OAuth token")
                        creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in token file: {token_path}")
                creds = None
            except Exception as e:
                logger.warning(f"Error loading credentials: {str(e)}")
                creds = None

    # If credentials don't exist or are invalid, get new ones
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("Refreshing expired token")
                creds.refresh(Request())
                logger.info("Token successfully refreshed")
            except RefreshError as e:
                logger.warning(f"Error refreshing token: {str(e)}, will try to get new token")
                creds = None
            except Exception as e:
                logger.error(f"Unexpected error refreshing token: {str(e)}")
                raise

        if not creds:
            if not client_config:
                logger.info("Creating OAuth client config from environment variables")
                client_id = os.environ.get("GOOGLE_ADS_CLIENT_ID")
                client_secret = os.environ.get("GOOGLE_ADS_CLIENT_SECRET")

                if not client_id or not client_secret:
                    raise ValueError(
                        "GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET must be set if no client config file exists"
                    )

                client_config = {
                    "installed": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
                    }
                }

            logger.info("Starting OAuth authentication flow")
            if os.environ.get("PORT") or os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("K_SERVICE"):
                raise ValueError(
                    "OAuth needs a local browser (InstalledAppFlow); it cannot run on this server. "
                    "Set GOOGLE_ADS_AUTH_TYPE=service_account and GOOGLE_ADS_CREDENTIALS_JSON to your "
                    "service account key JSON (one line). Invite that service account in Google Ads: "
                    "Tools & Settings > Access and security > Users."
                )
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)
            logger.info("OAuth flow completed successfully")

        try:
            logger.info(f"Saving credentials to {token_path}")
            parent = os.path.dirname(token_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(token_path, "w") as f:
                f.write(creds.to_json())
        except Exception as e:
            logger.warning(f"Could not save credentials: {str(e)}")

    return creds


def _get_bearer_token(creds):
    """Extract and refresh bearer token from credentials if needed."""
    if isinstance(creds, service_account.Credentials):
        creds.refresh(Request())
        return creds.token
    
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                logger.info("Refreshing expired OAuth token in get_headers")
                creds.refresh(Request())
                logger.info("Token successfully refreshed in get_headers")
            except RefreshError as e:
                logger.error(f"Error refreshing token in get_headers: {str(e)}")
                raise ValueError(f"Failed to refresh OAuth token: {str(e)}")
            except Exception as e:
                logger.error(f"Unexpected error refreshing token in get_headers: {str(e)}")
                raise
        else:
            raise ValueError("OAuth credentials are invalid and cannot be refreshed")
    return creds.token


def get_headers(creds, include_login_customer_id: bool = False):
    """Get headers for Google Ads API requests.

    Args:
        creds: OAuth or service account credentials.
        include_login_customer_id: Whether to include the login-customer-id header.
            Defaults to False because most client accounts are accessible without it.
            Set to True only when explicitly querying through an MCC manager account.
    """
    if not GOOGLE_ADS_DEVELOPER_TOKEN:
        raise ValueError("GOOGLE_ADS_DEVELOPER_TOKEN environment variable not set")

    token = _get_bearer_token(creds)

    headers = {
        'Authorization': f'Bearer {token}',
        'developer-token': GOOGLE_ADS_DEVELOPER_TOKEN,
        'content-type': 'application/json'
    }

    if include_login_customer_id and GOOGLE_ADS_LOGIN_CUSTOMER_ID:
        headers['login-customer-id'] = format_customer_id(GOOGLE_ADS_LOGIN_CUSTOMER_ID)

    return headers


def make_api_request(url: str, method: str = 'POST', payload: dict = None, creds=None):
    """Make a Google Ads API request, automatically retrying without login-customer-id on permission errors.
    
    Returns:
        (response_json, error_string) — one of which will be None.
    """
    if creds is None:
        creds = get_credentials()

    for include_login in (True, False):
        headers = get_headers(creds, include_login_customer_id=include_login)
        if method == 'GET':
            resp = requests.get(url, headers=headers)
        else:
            resp = requests.post(url, headers=headers, json=payload or {})

        if resp.status_code == 200:
            return resp.json(), None

        # On permission errors, retry without login-customer-id
        if include_login and resp.status_code == 403:
            try:
                err_code = (resp.json().get('error', {})
                            .get('details', [{}])[0]
                            .get('errors', [{}])[0]
                            .get('errorCode', {}))
                if 'USER_PERMISSION_DENIED' in err_code.values() or 'CUSTOMER_NOT_ENABLED' in err_code.values():
                    logger.info("Permission denied with login-customer-id, retrying without it")
                    continue
            except Exception:
                pass

        return None, resp.text

    return None, "Request failed after retrying without login-customer-id"

@mcp.tool()
async def list_accounts() -> str:
    """
    Lists all accessible Google Ads accounts.
    
    This is typically the first command you should run to identify which accounts 
    you have access to. The returned account IDs can be used in subsequent commands.
    
    Returns:
        A formatted list of all Google Ads accounts accessible with your credentials
    """
    try:
        creds = get_credentials()
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers:listAccessibleCustomers"
        data, error = make_api_request(url, method='GET', creds=creds)

        if error:
            return f"Error accessing accounts: {error}"

        customers = data
        if not customers.get('resourceNames'):
            return "No accessible accounts found."
        
        # Format the results
        result_lines = ["Accessible Google Ads Accounts:"]
        result_lines.append("-" * 50)
        
        for resource_name in customers['resourceNames']:
            customer_id = resource_name.split('/')[-1]
            formatted_id = format_customer_id(customer_id)
            result_lines.append(f"Account ID: {formatted_id}")
        
        return "\n".join(result_lines)
    
    except Exception as e:
        return f"Error listing accounts: {str(e)}"


def _gaql_search_raw(
    formatted_customer_id: str, query: str
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """
    POST googleAds:search for the given customer.

    Returns (rows, None) on HTTP 200 (rows may be empty). Returns (None, err) on failure.
    """
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        response = requests.post(
            url,
            headers=headers,
            json={"query": query},
            timeout=_google_ads_http_timeout_seconds(),
        )
        if response.status_code != 200:
            return None, f"Error executing query: {response.text}"
        body = response.json()
        rows = body.get("results") or []
        return rows, None
    except Exception as e:
        return None, f"Error executing GAQL query: {str(e)}"


def _mutations_disabled_by_env() -> bool:
    v = os.environ.get("GOOGLE_ADS_DISABLE_MUTATIONS", "").strip().lower()
    return v in ("1", "true", "yes")


def _mutate_validate_only_forced() -> bool:
    v = os.environ.get("GOOGLE_ADS_MUTATE_VALIDATE_ONLY", "").strip().lower()
    return v in ("1", "true", "yes")


def _post_with_login_retry(
    url: str, json_body: Dict[str, Any], creds=None
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    POST JSON to a Google Ads REST endpoint, retrying without login-customer-id
    on USER_PERMISSION_DENIED (same pattern as make_api_request).
    """
    if creds is None:
        creds = get_credentials()
    for include_login in (True, False):
        headers = get_headers(creds, include_login_customer_id=include_login)
        resp = requests.post(
            url,
            headers=headers,
            json=json_body,
            timeout=_google_ads_http_timeout_seconds(),
        )
        if resp.status_code == 200:
            return resp.json(), None
        if include_login and resp.status_code == 403:
            try:
                err = resp.json()
                details = err.get("error", {}).get("details", [{}])[0]
                errors = details.get("errors", [{}])[0]
                code = errors.get("errorCode", {})
                if (
                    code.get("authorizationError") == "USER_PERMISSION_DENIED"
                    or code.get("authorizationError") == "CUSTOMER_NOT_ENABLED"
                ):
                    logger.info(
                        "Permission denied with login-customer-id on mutate, retrying without it"
                    )
                    continue
            except Exception:
                pass
        return None, resp.text
    return None, "Request failed after retrying without login-customer-id"


def _format_mutate_response(label: str, body: Dict[str, Any]) -> str:
    """Readable summary for campaigns:mutate / campaignBudgets:mutate JSON."""
    lines = [label, "-" * 60]
    lines.append(json.dumps(body, indent=2, default=str))
    return "\n".join(lines)


def _format_execute_gaql_table(formatted_customer_id: str, rows: List[Dict[str, Any]]) -> str:
    """Same pipe-delimited layout as the historical execute_gaql_query table output."""
    result_lines = [f"Query Results for Account {formatted_customer_id}:"]
    result_lines.append("-" * 80)
    fields: List[str] = []
    first_result = rows[0]
    for key in first_result:
        if isinstance(first_result[key], dict):
            for subkey in first_result[key]:
                fields.append(f"{key}.{subkey}")
        else:
            fields.append(key)
    result_lines.append(" | ".join(fields))
    result_lines.append("-" * 80)
    for result in rows:
        row_data = []
        for field in fields:
            if "." in field:
                parent, child = field.split(".", 1)
                value = str(result.get(parent, {}).get(child, ""))
            else:
                value = str(result.get(field, ""))
            row_data.append(value)
        result_lines.append(" | ".join(row_data))
    return "\n".join(result_lines)


def campaign_performance_gaql(days: int) -> str:
    """GAQL for top campaigns by cost over LAST_N_DAYS (same as get_campaign_performance)."""
    return f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.average_cpc
        FROM campaign
        WHERE segments.date DURING LAST_{days}_DAYS
        ORDER BY metrics.cost_micros DESC
        LIMIT 50
    """


def _should_persist_campaign_performance_snapshot(persist_snapshot: bool) -> bool:
    """True if caller asked to persist or AUTO_PERSIST_CAMPAIGN_PERFORMANCE_SNAPSHOTS is set."""
    if persist_snapshot:
        return True
    v = os.environ.get("AUTO_PERSIST_CAMPAIGN_PERFORMANCE_SNAPSHOTS", "").strip().lower()
    return v in ("1", "true", "yes")


async def fetch_campaign_performance_table_and_rows(
    customer_id: str, days: int
) -> Dict[str, Any]:
    """
    Run the same GAQL as ``get_campaign_performance``; return table text and raw API rows.

    Keys: ``ok`` (bool), ``error`` (str if not ok), ``table`` (str), ``rows`` (list),
    ``formatted_customer_id`` (str).
    """
    formatted_customer_id = format_customer_id(customer_id)
    query = campaign_performance_gaql(days)
    rows, err = _gaql_search_raw(formatted_customer_id, query)
    if err:
        return {
            "ok": False,
            "error": err,
            "table": None,
            "rows": [],
            "formatted_customer_id": formatted_customer_id,
        }
    if not rows:
        return {
            "ok": True,
            "table": "No results found for the query.",
            "rows": [],
            "formatted_customer_id": formatted_customer_id,
        }
    return {
        "ok": True,
        "table": _format_execute_gaql_table(formatted_customer_id, rows),
        "rows": rows,
        "formatted_customer_id": formatted_customer_id,
    }


def ad_performance_gaql(days: int) -> str:
    """GAQL for top ads by impressions over LAST_N_DAYS (same as get_ad_performance)."""
    return f"""
        SELECT
            ad_group_ad.ad.id,
            ad_group_ad.ad.name,
            ad_group_ad.status,
            campaign.name,
            ad_group.name,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions
        FROM ad_group_ad
        WHERE segments.date DURING LAST_{days}_DAYS
        ORDER BY metrics.impressions DESC
        LIMIT 50
    """


async def fetch_ad_performance_table_and_rows(
    customer_id: str, days: int
) -> Dict[str, Any]:
    """Same shape as ``fetch_campaign_performance_table_and_rows`` for ad-level rows."""
    formatted_customer_id = format_customer_id(customer_id)
    query = ad_performance_gaql(days)
    rows, err = _gaql_search_raw(formatted_customer_id, query)
    if err:
        return {
            "ok": False,
            "error": err,
            "table": None,
            "rows": [],
            "formatted_customer_id": formatted_customer_id,
        }
    if not rows:
        return {
            "ok": True,
            "table": "No results found for the query.",
            "rows": [],
            "formatted_customer_id": formatted_customer_id,
        }
    return {
        "ok": True,
        "table": _format_execute_gaql_table(formatted_customer_id, rows),
        "rows": rows,
        "formatted_customer_id": formatted_customer_id,
    }


def ad_copy_asset_performance_gaql(days: int) -> str:
    """GAQL for RSA text assets with performance labels and metrics (top 200 by impressions)."""
    return f"""
        SELECT
            campaign.name,
            ad_group.name,
            ad_group_ad.ad.id,
            asset.id,
            asset.text_asset.text,
            ad_group_ad_asset_view.field_type,
            ad_group_ad_asset_view.performance_label,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions,
            metrics.cost_micros,
            metrics.ctr
        FROM ad_group_ad_asset_view
        WHERE segments.date DURING LAST_{days}_DAYS
            AND ad_group_ad_asset_view.field_type IN ('HEADLINE', 'DESCRIPTION')
            AND metrics.impressions > 0
        ORDER BY metrics.impressions DESC
        LIMIT 200
    """


def ad_copy_asset_performance_fallback_gaql(days: int) -> str:
    """Fallback when ad_group_ad_asset_view has no rows (account / API shape)."""
    return f"""
        SELECT
            campaign.name,
            ad_group.name,
            asset.id,
            asset.type,
            asset.text_asset.text,
            asset_performance_label,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions,
            metrics.cost_micros,
            metrics.ctr
        FROM asset_performance_label_view
        WHERE asset.type = 'TEXT'
            AND segments.date DURING LAST_{days}_DAYS
            AND metrics.impressions > 0
        ORDER BY metrics.impressions DESC
        LIMIT 200
    """


async def fetch_ad_copy_asset_performance_rows(
    customer_id: str, days: int
) -> Dict[str, Any]:
    """
    Text RSA assets with performance labels and metrics.

    Tries ``ad_group_ad_asset_view`` first (headline vs description), then
    ``asset_performance_label_view``. Same return shape as other ``fetch_*`` helpers.
    """
    formatted_customer_id = format_customer_id(customer_id)
    primary_query = ad_copy_asset_performance_gaql(days)
    rows, err = _gaql_search_raw(formatted_customer_id, primary_query)
    if err:
        return {
            "ok": False,
            "error": err,
            "table": None,
            "rows": [],
            "formatted_customer_id": formatted_customer_id,
            "source": "ad_group_ad_asset_view",
        }
    if rows:
        return {
            "ok": True,
            "table": _format_execute_gaql_table(formatted_customer_id, rows),
            "rows": rows,
            "formatted_customer_id": formatted_customer_id,
            "source": "ad_group_ad_asset_view",
        }

    fallback_query = ad_copy_asset_performance_fallback_gaql(days)
    rows2, err2 = _gaql_search_raw(formatted_customer_id, fallback_query)
    if err2:
        return {
            "ok": False,
            "error": err2,
            "table": None,
            "rows": [],
            "formatted_customer_id": formatted_customer_id,
            "source": "asset_performance_label_view",
        }
    if not rows2:
        return {
            "ok": True,
            "table": "No text asset performance rows for this period.",
            "rows": [],
            "formatted_customer_id": formatted_customer_id,
            "source": "none",
        }
    return {
        "ok": True,
        "table": _format_execute_gaql_table(formatted_customer_id, rows2),
        "rows": rows2,
        "formatted_customer_id": formatted_customer_id,
        "source": "asset_performance_label_view",
    }


@mcp.tool()
async def execute_gaql_query(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    query: str = Field(description="Valid GAQL query string following Google Ads Query Language syntax")
) -> str:
    """
    Execute a custom GAQL (Google Ads Query Language) query.
    
    This tool allows you to run any valid GAQL query against the Google Ads API.
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        query: The GAQL query to execute (must follow GAQL syntax)
        
    Returns:
        Formatted query results or error message
        
    Example:
        customer_id: "1234567890"
        query: "SELECT campaign.id, campaign.name FROM campaign LIMIT 10"
    """
    formatted_customer_id = format_customer_id(customer_id)
    rows, err = _gaql_search_raw(formatted_customer_id, query)
    if err:
        return err
    if not rows:
        return "No results found for the query."
    return _format_execute_gaql_table(formatted_customer_id, rows)

@mcp.tool()
async def get_campaign_performance(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    days: int = Field(default=30, description="Number of days to look back (7, 30, 90, etc.)"),
    persist_snapshot: bool = Field(
        default=False,
        description=(
            "When True, save this fetch to Supabase report_snapshots (requires SUPABASE_*). "
            "Or set env AUTO_PERSIST_CAMPAIGN_PERFORMANCE_SNAPSHOTS=1 to persist without passing True each time."
        ),
    ),
) -> str:
    """
    Get campaign performance metrics for the specified time period.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Then run get_account_currency() to see what currency the account uses
    3. Finally run this command to get campaign performance
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        days: Number of days to look back (default: 30)
        persist_snapshot: Save a snapshot to Supabase when configured (weekly/monthly style: use days=7 or 30).
        
    Returns:
        Formatted table of campaign performance data
        
    Note:
        Cost values are in micros (millionths) of the account currency
        (e.g., 1000000 = 1 USD in a USD account)
        
    Example:
        customer_id: "1234567890"
        days: 14
    """
    data = await fetch_campaign_performance_table_and_rows(customer_id, days)
    if not data["ok"]:
        return str(data["error"])
    table = data["table"]
    rows: List[Dict[str, Any]] = data["rows"]
    formatted_customer_id: str = data["formatted_customer_id"]

    if not rows:
        return table if table is not None else "No results found for the query."

    if _should_persist_campaign_performance_snapshot(persist_snapshot):
        try:
            import supabase_store as store

            if store.is_configured():
                snap_out = store.persist_campaign_performance_snapshot(
                    customer_id=formatted_customer_id,
                    days=days,
                    api_results=rows,
                    summary=None,
                )
                return (
                    table
                    + "\n\n---\nSupabase snapshot: "
                    + json.dumps(snap_out, default=str)
                )
        except Exception as e:
            return (
                table
                + "\n\n---\nSupabase snapshot (error): "
                + json.dumps({"error": str(e)}, default=str)
            )

    return table

@mcp.tool()
async def get_ad_performance(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    days: int = Field(default=30, description="Number of days to look back (7, 30, 90, etc.)")
) -> str:
    """
    Get ad performance metrics for the specified time period.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Then run get_account_currency() to see what currency the account uses
    3. Finally run this command to get ad performance
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        days: Number of days to look back (default: 30)
        
    Returns:
        Formatted table of ad performance data
        
    Note:
        Cost values are in micros (millionths) of the account currency
        (e.g., 1000000 = 1 USD in a USD account)
        
    Example:
        customer_id: "1234567890"
        days: 14
    """
    query = f"""
        SELECT
            ad_group_ad.ad.id,
            ad_group_ad.ad.name,
            ad_group_ad.status,
            campaign.name,
            ad_group.name,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions
        FROM ad_group_ad
        WHERE segments.date DURING LAST_{days}_DAYS
        ORDER BY metrics.impressions DESC
        LIMIT 50
    """
    
    return await execute_gaql_query(customer_id, query)

@mcp.tool()
async def run_gaql(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    query: str = Field(description="Valid GAQL query string following Google Ads Query Language syntax"),
    format: str = Field(default="table", description="Output format: 'table', 'json', or 'csv'")
) -> str:
    """
    Execute any arbitrary GAQL (Google Ads Query Language) query with custom formatting options.
    
    This is the most powerful tool for custom Google Ads data queries.
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        query: The GAQL query to execute (any valid GAQL query)
        format: Output format ("table", "json", or "csv")
    
    Returns:
        Query results in the requested format
    
    EXAMPLE QUERIES:
    
    1. Basic campaign metrics:
        SELECT 
          campaign.name, 
          metrics.clicks, 
          metrics.impressions,
          metrics.cost_micros
        FROM campaign 
        WHERE segments.date DURING LAST_7_DAYS
    
    2. Ad group performance:
        SELECT 
          ad_group.name, 
          metrics.conversions, 
          metrics.cost_micros,
          campaign.name
        FROM ad_group 
        WHERE metrics.clicks > 100
    
    3. Keyword analysis:
        SELECT 
          keyword.text, 
          metrics.average_position, 
          metrics.ctr
        FROM keyword_view 
        ORDER BY metrics.impressions DESC
        
    4. Get conversion data:
        SELECT
          campaign.name,
          metrics.conversions,
          metrics.conversions_value,
          metrics.cost_micros
        FROM campaign
        WHERE segments.date DURING LAST_30_DAYS
        
            Note:
        Cost values are in micros (millionths) of the account currency
        (e.g., 1000000 = 1 USD in a USD account)
    """
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error executing query: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return "No results found for the query."
        
        if format.lower() == "json":
            return json.dumps(results, indent=2)
        
        elif format.lower() == "csv":
            # Get field names from the first result
            fields = []
            first_result = results['results'][0]
            for key, value in first_result.items():
                if isinstance(value, dict):
                    for subkey in value:
                        fields.append(f"{key}.{subkey}")
                else:
                    fields.append(key)
            
            # Create CSV string
            csv_lines = [",".join(fields)]
            for result in results['results']:
                row_data = []
                for field in fields:
                    if "." in field:
                        parent, child = field.split(".")
                        value = str(result.get(parent, {}).get(child, "")).replace(",", ";")
                    else:
                        value = str(result.get(field, "")).replace(",", ";")
                    row_data.append(value)
                csv_lines.append(",".join(row_data))
            
            return "\n".join(csv_lines)
        
        else:  # default table format
            result_lines = [f"Query Results for Account {formatted_customer_id}:"]
            result_lines.append("-" * 100)
            
            # Get field names and maximum widths
            fields = []
            field_widths = {}
            first_result = results['results'][0]
            
            for key, value in first_result.items():
                if isinstance(value, dict):
                    for subkey in value:
                        field = f"{key}.{subkey}"
                        fields.append(field)
                        field_widths[field] = len(field)
                else:
                    fields.append(key)
                    field_widths[key] = len(key)
            
            # Calculate maximum field widths
            for result in results['results']:
                for field in fields:
                    if "." in field:
                        parent, child = field.split(".")
                        value = str(result.get(parent, {}).get(child, ""))
                    else:
                        value = str(result.get(field, ""))
                    field_widths[field] = max(field_widths[field], len(value))
            
            # Create formatted header
            header = " | ".join(f"{field:{field_widths[field]}}" for field in fields)
            result_lines.append(header)
            result_lines.append("-" * len(header))
            
            # Add data rows
            for result in results['results']:
                row_data = []
                for field in fields:
                    if "." in field:
                        parent, child = field.split(".")
                        value = str(result.get(parent, {}).get(child, ""))
                    else:
                        value = str(result.get(field, ""))
                    row_data.append(f"{value:{field_widths[field]}}")
                result_lines.append(" | ".join(row_data))
            
            return "\n".join(result_lines)
    
    except Exception as e:
        return f"Error executing GAQL query: {str(e)}"

@mcp.tool()
async def get_ad_creatives(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'")
) -> str:
    """
    Get ad creative details including headlines, descriptions, and URLs.
    
    This tool retrieves the actual ad content (headlines, descriptions) 
    for review and analysis. Great for creative audits.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Then run this command with the desired account ID
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        
    Returns:
        Formatted list of ad creative details
        
    Example:
        customer_id: "1234567890"
    """
    query = """
        SELECT
            ad_group_ad.ad.id,
            ad_group_ad.ad.name,
            ad_group_ad.ad.type,
            ad_group_ad.ad.final_urls,
            ad_group_ad.status,
            ad_group_ad.ad.responsive_search_ad.headlines,
            ad_group_ad.ad.responsive_search_ad.descriptions,
            ad_group.name,
            campaign.name
        FROM ad_group_ad
        WHERE ad_group_ad.status != 'REMOVED'
        ORDER BY campaign.name, ad_group.name
        LIMIT 50
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving ad creatives: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return "No ad creatives found for this customer ID."
        
        # Format the results in a readable way
        output_lines = [f"Ad Creatives for Customer ID {formatted_customer_id}:"]
        output_lines.append("=" * 80)
        
        for i, result in enumerate(results['results'], 1):
            ad = result.get('adGroupAd', {}).get('ad', {})
            ad_group = result.get('adGroup', {})
            campaign = result.get('campaign', {})
            
            output_lines.append(f"\n{i}. Campaign: {campaign.get('name', 'N/A')}")
            output_lines.append(f"   Ad Group: {ad_group.get('name', 'N/A')}")
            output_lines.append(f"   Ad ID: {ad.get('id', 'N/A')}")
            output_lines.append(f"   Ad Name: {ad.get('name', 'N/A')}")
            output_lines.append(f"   Status: {result.get('adGroupAd', {}).get('status', 'N/A')}")
            output_lines.append(f"   Type: {ad.get('type', 'N/A')}")
            
            # Handle Responsive Search Ads
            rsa = ad.get('responsiveSearchAd', {})
            if rsa:
                if 'headlines' in rsa:
                    output_lines.append("   Headlines:")
                    for headline in rsa['headlines']:
                        output_lines.append(f"     - {headline.get('text', 'N/A')}")
                
                if 'descriptions' in rsa:
                    output_lines.append("   Descriptions:")
                    for desc in rsa['descriptions']:
                        output_lines.append(f"     - {desc.get('text', 'N/A')}")
            
            # Handle Final URLs
            final_urls = ad.get('finalUrls', [])
            if final_urls:
                output_lines.append(f"   Final URLs: {', '.join(final_urls)}")
            
            output_lines.append("-" * 80)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error retrieving ad creatives: {str(e)}"

@mcp.tool()
async def get_account_currency(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'")
) -> str:
    """
    Retrieve the default currency code used by the Google Ads account.
    
    IMPORTANT: Run this first before analyzing cost data to understand which currency
    the account uses. Cost values are always displayed in the account's currency.
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
    
    Returns:
        The account's default currency code (e.g., 'USD', 'EUR', 'GBP')
        
    Example:
        customer_id: "1234567890"
    """
    query = """
        SELECT
            customer.id,
            customer.currency_code
        FROM customer
        LIMIT 1
    """
    
    try:
        creds = get_credentials()
        
        # Force refresh if needed
        if not creds.valid:
            logger.info("Credentials not valid, attempting refresh...")
            if hasattr(creds, 'refresh_token') and creds.refresh_token:
                creds.refresh(Request())
                logger.info("Credentials refreshed successfully")
            else:
                raise ValueError("Invalid credentials and no refresh token available")
        
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving account currency: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return "No account information found for this customer ID."
        
        # Extract the currency code from the results
        customer = results['results'][0].get('customer', {})
        currency_code = customer.get('currencyCode', 'Not specified')
        
        return f"Account {formatted_customer_id} uses currency: {currency_code}"
    
    except Exception as e:
        logger.error(f"Error retrieving account currency: {str(e)}")
        return f"Error retrieving account currency: {str(e)}"

@mcp.resource("gaql://reference")
def gaql_reference() -> str:
    """Google Ads Query Language (GAQL) reference documentation."""
    return """
    # Google Ads Query Language (GAQL) Reference
    
    GAQL is similar to SQL but with specific syntax for Google Ads. Here's a quick reference:
    
    ## Basic Query Structure
    ```
    SELECT field1, field2, ... 
    FROM resource_type
    WHERE condition
    ORDER BY field [ASC|DESC]
    LIMIT n
    ```
    
    ## Common Field Types
    
    ### Resource Fields
    - campaign.id, campaign.name, campaign.status
    - ad_group.id, ad_group.name, ad_group.status
    - ad_group_ad.ad.id, ad_group_ad.ad.final_urls
    - keyword.text, keyword.match_type
    
    ### Metric Fields
    - metrics.impressions
    - metrics.clicks
    - metrics.cost_micros
    - metrics.conversions
    - metrics.ctr
    - metrics.average_cpc
    
    ### Segment Fields
    - segments.date
    - segments.device
    - segments.day_of_week
    
    ## Common WHERE Clauses
    
    ### Date Ranges
    - WHERE segments.date DURING LAST_7_DAYS
    - WHERE segments.date DURING LAST_30_DAYS
    - WHERE segments.date BETWEEN '2023-01-01' AND '2023-01-31'
    
    ### Filtering
    - WHERE campaign.status = 'ENABLED'
    - WHERE metrics.clicks > 100
    - WHERE campaign.name LIKE '%Brand%'
    
    ## Tips
    - Always check account currency before analyzing cost data
    - Cost values are in micros (millionths): 1000000 = 1 unit of currency
    - Use LIMIT to avoid large result sets
    """

@mcp.prompt("google_ads_workflow")
def google_ads_workflow() -> str:
    """Provides guidance on the recommended workflow for using Google Ads tools."""
    return """
    I'll help you analyze your Google Ads account data. Here's the recommended workflow:
    
    1. First, let's list all the accounts you have access to:
       - Run the `list_accounts()` tool to get available account IDs
    
    2. Before analyzing cost data, let's check which currency the account uses:
       - Run `get_account_currency(customer_id="ACCOUNT_ID")` with your selected account
    
    3. Now we can explore the account data:
       - For campaign performance: `get_campaign_performance(customer_id="ACCOUNT_ID", days=30)`
       - For ad performance: `get_ad_performance(customer_id="ACCOUNT_ID", days=30)`
       - For ad creative review: `get_ad_creatives(customer_id="ACCOUNT_ID")`
    
    4. For custom queries, use the GAQL query tool:
       - `run_gaql(customer_id="ACCOUNT_ID", query="YOUR_QUERY", format="table")`
    
    5. Let me know if you have specific questions about:
       - Campaign performance
       - Ad performance
       - Keywords
       - Budgets
       - Conversions
    
    Important: Always provide the customer_id as a string.
    For example: customer_id="1234567890"
    """

@mcp.prompt("gaql_help")
def gaql_help() -> str:
    """Provides assistance for writing GAQL queries."""
    return """
    I'll help you write a Google Ads Query Language (GAQL) query. Here are some examples to get you started:
    
    ## Get campaign performance last 30 days
    ```
    SELECT
      campaign.id,
      campaign.name,
      campaign.status,
      metrics.impressions,
      metrics.clicks,
      metrics.cost_micros,
      metrics.conversions
    FROM campaign
    WHERE segments.date DURING LAST_30_DAYS
    ORDER BY metrics.cost_micros DESC
    ```
    
    ## Get keyword performance
    ```
    SELECT
      keyword.text,
      keyword.match_type,
      metrics.impressions,
      metrics.clicks,
      metrics.cost_micros,
      metrics.conversions
    FROM keyword_view
    WHERE segments.date DURING LAST_30_DAYS
    ORDER BY metrics.clicks DESC
    ```
    
    ## Get ads with poor performance
    ```
    SELECT
      ad_group_ad.ad.id,
      ad_group_ad.ad.name,
      campaign.name,
      ad_group.name,
      metrics.impressions,
      metrics.clicks,
      metrics.conversions
    FROM ad_group_ad
    WHERE 
      segments.date DURING LAST_30_DAYS
      AND metrics.impressions > 1000
      AND metrics.ctr < 0.01
    ORDER BY metrics.impressions DESC
    ```
    
    Once you've chosen a query, use it with:
    ```
    run_gaql(customer_id="YOUR_ACCOUNT_ID", query="YOUR_QUERY_HERE")
    ```
    
    Remember:
    - Always provide the customer_id as a string
    - Cost values are in micros (1,000,000 = 1 unit of currency)
    - Use LIMIT to avoid large result sets
    - Check the account currency before analyzing cost data
    """

@mcp.tool()
async def get_image_assets(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    limit: int = Field(default=50, description="Maximum number of image assets to return")
) -> str:
    """
    Retrieve all image assets in the account including their full-size URLs.
    
    This tool allows you to get details about image assets used in your Google Ads account,
    including the URLs to download the full-size images for further processing or analysis.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Then run this command with the desired account ID
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        limit: Maximum number of image assets to return (default: 50)
        
    Returns:
        Formatted list of image assets with their download URLs
        
    Example:
        customer_id: "1234567890"
        limit: 100
    """
    query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.type,
            asset.image_asset.full_size.url,
            asset.image_asset.full_size.height_pixels,
            asset.image_asset.full_size.width_pixels,
            asset.image_asset.file_size
        FROM
            asset
        WHERE
            asset.type = 'IMAGE'
        LIMIT {limit}
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving image assets: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return "No image assets found for this customer ID."
        
        # Format the results in a readable way
        output_lines = [f"Image Assets for Customer ID {formatted_customer_id}:"]
        output_lines.append("=" * 80)
        
        for i, result in enumerate(results['results'], 1):
            asset = result.get('asset', {})
            image_asset = asset.get('imageAsset', {})
            full_size = image_asset.get('fullSize', {})
            
            output_lines.append(f"\n{i}. Asset ID: {asset.get('id', 'N/A')}")
            output_lines.append(f"   Name: {asset.get('name', 'N/A')}")
            
            if full_size:
                output_lines.append(f"   Image URL: {full_size.get('url', 'N/A')}")
                output_lines.append(f"   Dimensions: {full_size.get('widthPixels', 'N/A')} x {full_size.get('heightPixels', 'N/A')} px")
            
            file_size = image_asset.get('fileSize', 'N/A')
            if file_size != 'N/A':
                # Convert to KB for readability
                file_size_kb = int(file_size) / 1024
                output_lines.append(f"   File Size: {file_size_kb:.2f} KB")
            
            output_lines.append("-" * 80)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error retrieving image assets: {str(e)}"

@mcp.tool()
async def download_image_asset(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    asset_id: str = Field(description="The ID of the image asset to download"),
    output_dir: str = Field(default="./ad_images", description="Directory to save the downloaded image")
) -> str:
    """
    Download a specific image asset from a Google Ads account.
    
    This tool allows you to download the full-size version of an image asset
    for further processing, analysis, or backup.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Then run get_image_assets() to get available image asset IDs
    3. Finally use this command to download specific images
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        asset_id: The ID of the image asset to download
        output_dir: Directory where the image should be saved (default: ./ad_images)
        
    Returns:
        Status message indicating success or failure of the download
        
    Example:
        customer_id: "1234567890"
        asset_id: "12345"
        output_dir: "./my_ad_images"
    """
    query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.image_asset.full_size.url
        FROM
            asset
        WHERE
            asset.type = 'IMAGE'
            AND asset.id = {asset_id}
        LIMIT 1
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving image asset: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return f"No image asset found with ID {asset_id}"
        
        # Extract the image URL
        asset = results['results'][0].get('asset', {})
        image_url = asset.get('imageAsset', {}).get('fullSize', {}).get('url')
        asset_name = asset.get('name', f"image_{asset_id}")
        
        if not image_url:
            return f"No download URL found for image asset ID {asset_id}"
        
        # Validate and sanitize the output directory to prevent path traversal
        try:
            # Get the base directory (current working directory)
            base_dir = Path.cwd()
            # Resolve the output directory to an absolute path
            resolved_output_dir = Path(output_dir).resolve()
            
            # Ensure the resolved path is within or under the current working directory
            # This prevents path traversal attacks like "../../../etc"
            try:
                resolved_output_dir.relative_to(base_dir)
            except ValueError:
                # If the path is not relative to base_dir, use the default safe directory
                resolved_output_dir = base_dir / "ad_images"
                logger.warning(f"Invalid output directory '{output_dir}' - using default './ad_images'")
            
            # Create output directory if it doesn't exist
            resolved_output_dir.mkdir(parents=True, exist_ok=True)
            
        except Exception as e:
            return f"Error creating output directory: {str(e)}"
        
        # Download the image
        image_response = requests.get(image_url)
        if image_response.status_code != 200:
            return f"Failed to download image: HTTP {image_response.status_code}"
        
        # Clean the filename to be safe for filesystem
        safe_name = ''.join(c for c in asset_name if c.isalnum() or c in ' ._-')
        filename = f"{asset_id}_{safe_name}.jpg"
        file_path = resolved_output_dir / filename
        
        # Save the image
        with open(file_path, 'wb') as f:
            f.write(image_response.content)
        
        return f"Successfully downloaded image asset {asset_id} to {file_path}"
    
    except Exception as e:
        return f"Error downloading image asset: {str(e)}"

@mcp.tool()
async def get_asset_usage(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    asset_id: str = Field(default=None, description="Optional: specific asset ID to look up (leave empty to get all image assets)"),
    asset_type: str = Field(default="IMAGE", description="Asset type to search for ('IMAGE', 'TEXT', 'VIDEO', etc.)")
) -> str:
    """
    Find where specific assets are being used in campaigns, ad groups, and ads.
    
    This tool helps you analyze how assets are linked to campaigns and ads across your account,
    which is useful for creative analysis and optimization.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Run get_image_assets() to see available assets
    3. Use this command to see where specific assets are used
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        asset_id: Optional specific asset ID to look up (leave empty to get all assets of the specified type)
        asset_type: Type of asset to search for (default: 'IMAGE')
        
    Returns:
        Formatted report showing where assets are used in the account
        
    Example:
        customer_id: "1234567890"
        asset_id: "12345"
        asset_type: "IMAGE"
    """
    # Build the query based on whether a specific asset ID was provided
    where_clause = f"asset.type = '{asset_type}'"
    if asset_id:
        where_clause += f" AND asset.id = {asset_id}"
    
    # First get the assets themselves
    assets_query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.type
        FROM
            asset
        WHERE
            {where_clause}
        LIMIT 100
    """
    
    # Then get the associations between assets and campaigns/ad groups
    # Try using campaign_asset instead of asset_link
    associations_query = f"""
        SELECT
            campaign.id,
            campaign.name,
            asset.id,
            asset.name,
            asset.type
        FROM
            campaign_asset
        WHERE
            {where_clause}
        LIMIT 500
    """

    # Also try ad_group_asset for ad group level information
    ad_group_query = f"""
        SELECT
            ad_group.id,
            ad_group.name,
            asset.id,
            asset.name,
            asset.type
        FROM
            ad_group_asset
        WHERE
            {where_clause}
        LIMIT 500
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        
        # First get the assets
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        payload = {"query": assets_query}
        assets_response = requests.post(url, headers=headers, json=payload)
        
        if assets_response.status_code != 200:
            return f"Error retrieving assets: {assets_response.text}"
        
        assets_results = assets_response.json()
        if not assets_results.get('results'):
            return f"No {asset_type} assets found for this customer ID."
        
        # Now get the associations
        payload = {"query": associations_query}
        assoc_response = requests.post(url, headers=headers, json=payload)
        
        if assoc_response.status_code != 200:
            return f"Error retrieving asset associations: {assoc_response.text}"
        
        assoc_results = assoc_response.json()
        
        # Format the results in a readable way
        output_lines = [f"Asset Usage for Customer ID {formatted_customer_id}:"]
        output_lines.append("=" * 80)
        
        # Create a dictionary to organize asset usage by asset ID
        asset_usage = {}
        
        # Initialize the asset usage dictionary with basic asset info
        for result in assets_results.get('results', []):
            asset = result.get('asset', {})
            asset_id = asset.get('id')
            if asset_id:
                asset_usage[asset_id] = {
                    'name': asset.get('name', 'Unnamed asset'),
                    'type': asset.get('type', 'Unknown'),
                    'usage': []
                }
        
        # Add usage information from the associations
        for result in assoc_results.get('results', []):
            asset = result.get('asset', {})
            asset_id = asset.get('id')
            
            if asset_id and asset_id in asset_usage:
                campaign = result.get('campaign', {})
                ad_group = result.get('adGroup', {})
                ad = result.get('adGroupAd', {}).get('ad', {}) if 'adGroupAd' in result else {}
                asset_link = result.get('assetLink', {})
                
                usage_info = {
                    'campaign_id': campaign.get('id', 'N/A'),
                    'campaign_name': campaign.get('name', 'N/A'),
                    'ad_group_id': ad_group.get('id', 'N/A'),
                    'ad_group_name': ad_group.get('name', 'N/A'),
                    'ad_id': ad.get('id', 'N/A') if ad else 'N/A',
                    'ad_name': ad.get('name', 'N/A') if ad else 'N/A'
                }
                
                asset_usage[asset_id]['usage'].append(usage_info)
        
        # Format the output
        for asset_id, info in asset_usage.items():
            output_lines.append(f"\nAsset ID: {asset_id}")
            output_lines.append(f"Name: {info['name']}")
            output_lines.append(f"Type: {info['type']}")
            
            if info['usage']:
                output_lines.append("\nUsed in:")
                output_lines.append("-" * 60)
                output_lines.append(f"{'Campaign':<30} | {'Ad Group':<30}")
                output_lines.append("-" * 60)
                
                for usage in info['usage']:
                    campaign_str = f"{usage['campaign_name']} ({usage['campaign_id']})"
                    ad_group_str = f"{usage['ad_group_name']} ({usage['ad_group_id']})"
                    
                    output_lines.append(f"{campaign_str[:30]:<30} | {ad_group_str[:30]:<30}")
            
            output_lines.append("=" * 80)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error retrieving asset usage: {str(e)}"

@mcp.tool()
async def analyze_image_assets(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    days: int = Field(default=30, description="Number of days to look back (7, 30, 90, etc.)")
) -> str:
    """
    Analyze image assets with their performance metrics across campaigns.
    
    This comprehensive tool helps you understand which image assets are performing well
    by showing metrics like impressions, clicks, and conversions for each image.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Then run get_account_currency() to see what currency the account uses
    3. Finally run this command to analyze image asset performance
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        days: Number of days to look back (default: 30)
        
    Returns:
        Detailed report of image assets and their performance metrics
        
    Example:
        customer_id: "1234567890"
        days: 14
    """
    # Make sure to use a valid date range format
    # Valid formats are: LAST_7_DAYS, LAST_14_DAYS, LAST_30_DAYS, etc. (with underscores)
    if days == 7:
        date_range = "LAST_7_DAYS"
    elif days == 14:
        date_range = "LAST_14_DAYS"
    elif days == 30:
        date_range = "LAST_30_DAYS"
    else:
        # Default to 30 days if not a standard range
        date_range = "LAST_30_DAYS"
        
    query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.image_asset.full_size.url,
            asset.image_asset.full_size.width_pixels,
            asset.image_asset.full_size.height_pixels,
            campaign.name,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions,
            metrics.cost_micros
        FROM
            campaign_asset
        WHERE
            asset.type = 'IMAGE'
            AND segments.date DURING LAST_30_DAYS
        ORDER BY
            metrics.impressions DESC
        LIMIT 200
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error analyzing image assets: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return "No image asset performance data found for this customer ID and time period."
        
        # Group results by asset ID
        assets_data = {}
        for result in results.get('results', []):
            asset = result.get('asset', {})
            asset_id = asset.get('id')
            
            if asset_id not in assets_data:
                assets_data[asset_id] = {
                    'name': asset.get('name', f"Asset {asset_id}"),
                    'url': asset.get('imageAsset', {}).get('fullSize', {}).get('url', 'N/A'),
                    'dimensions': f"{asset.get('imageAsset', {}).get('fullSize', {}).get('widthPixels', 'N/A')} x {asset.get('imageAsset', {}).get('fullSize', {}).get('heightPixels', 'N/A')}",
                    'impressions': 0,
                    'clicks': 0,
                    'conversions': 0,
                    'cost_micros': 0,
                    'campaigns': set(),
                    'ad_groups': set()
                }
            
            # Aggregate metrics
            metrics = result.get('metrics', {})
            assets_data[asset_id]['impressions'] += int(metrics.get('impressions', 0))
            assets_data[asset_id]['clicks'] += int(metrics.get('clicks', 0))
            assets_data[asset_id]['conversions'] += float(metrics.get('conversions', 0))
            assets_data[asset_id]['cost_micros'] += int(metrics.get('costMicros', 0))
            
            # Add campaign and ad group info
            campaign = result.get('campaign', {})
            ad_group = result.get('adGroup', {})
            
            if campaign.get('name'):
                assets_data[asset_id]['campaigns'].add(campaign.get('name'))
            if ad_group.get('name'):
                assets_data[asset_id]['ad_groups'].add(ad_group.get('name'))
        
        # Format the results
        output_lines = [f"Image Asset Performance Analysis for Customer ID {formatted_customer_id} (Last {days} days):"]
        output_lines.append("=" * 100)
        
        # Sort assets by impressions (highest first)
        sorted_assets = sorted(assets_data.items(), key=lambda x: x[1]['impressions'], reverse=True)
        
        for asset_id, data in sorted_assets:
            output_lines.append(f"\nAsset ID: {asset_id}")
            output_lines.append(f"Name: {data['name']}")
            output_lines.append(f"Dimensions: {data['dimensions']}")
            
            # Calculate CTR if there are impressions
            ctr = (data['clicks'] / data['impressions'] * 100) if data['impressions'] > 0 else 0
            
            # Format metrics
            output_lines.append(f"\nPerformance Metrics:")
            output_lines.append(f"  Impressions: {data['impressions']:,}")
            output_lines.append(f"  Clicks: {data['clicks']:,}")
            output_lines.append(f"  CTR: {ctr:.2f}%")
            output_lines.append(f"  Conversions: {data['conversions']:.2f}")
            output_lines.append(f"  Cost (micros): {data['cost_micros']:,}")
            
            # Show where it's used
            output_lines.append(f"\nUsed in {len(data['campaigns'])} campaigns:")
            for campaign in list(data['campaigns'])[:5]:  # Show first 5 campaigns
                output_lines.append(f"  - {campaign}")
            if len(data['campaigns']) > 5:
                output_lines.append(f"  - ... and {len(data['campaigns']) - 5} more")
            
            # Add URL
            if data['url'] != 'N/A':
                output_lines.append(f"\nImage URL: {data['url']}")
            
            output_lines.append("-" * 100)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error analyzing image assets: {str(e)}"

@mcp.tool()
async def list_resources(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'")
) -> str:
    """
    List valid resources that can be used in GAQL FROM clauses.
    
    Args:
        customer_id: The Google Ads customer ID as a string
        
    Returns:
        Formatted list of valid resources
    """
    # Example query that lists some common resources
    # This might need to be adjusted based on what's available in your API version
    query = """
        SELECT
            google_ads_field.name,
            google_ads_field.category,
            google_ads_field.data_type
        FROM
            google_ads_field
        WHERE
            google_ads_field.category = 'RESOURCE'
        ORDER BY
            google_ads_field.name
    """
    
    # Use your existing run_gaql function to execute this query
    return await run_gaql(customer_id, query)


@mcp.tool()
async def update_search_campaign(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    campaign_id: str = Field(
        description="Numeric campaign.id for the campaign to update (digits only; dashes allowed and stripped)"
    ),
    status: Optional[str] = Field(
        default=None,
        description="New campaign status: ENABLED or PAUSED. Omit if you only change name.",
    ),
    name: Optional[str] = Field(
        default=None,
        description="New campaign name. Omit if you only change status.",
    ),
    validate_only: bool = Field(
        default=False,
        description="If True, the API validates the mutate but does not apply it (dry run).",
    ),
    allow_non_search: bool = Field(
        default=False,
        description="If False (default), refuse when advertising_channel_type is not SEARCH.",
    ),
) -> str:
    """
    Update an existing **Search** campaign via the Google Ads REST **campaigns:mutate** endpoint.

    Supported sparse updates: **status**, **name** (at least one required). Use **validate_only**
    to dry-run. Set env **GOOGLE_ADS_DISABLE_MUTATIONS=1** to block all mutates. Set
    **GOOGLE_ADS_MUTATE_VALIDATE_ONLY=1** to force validate-only for every call.

    REST reference: ``POST customers/{customerId}/campaigns:mutate`` with an ``operations`` array.
    """
    if _mutations_disabled_by_env():
        return "Mutations are disabled (GOOGLE_ADS_DISABLE_MUTATIONS=1). Remove or unset to allow updates."

    if not status and not name:
        return "Error: provide at least one of `status` or `name`."

    if status and str(status).upper() not in ("ENABLED", "PAUSED"):
        return "Error: `status` must be ENABLED or PAUSED."

    if _mutate_validate_only_forced():
        validate_only = True

    formatted_customer_id = format_customer_id(customer_id)
    cid_digits = "".join(ch for ch in str(campaign_id) if ch.isdigit())
    if not cid_digits:
        return "Error: campaign_id must contain digits."

    pre_query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.advertising_channel_type
        FROM campaign
        WHERE campaign.id = {cid_digits}
        LIMIT 1
    """
    rows, err = _gaql_search_raw(formatted_customer_id, pre_query)
    if err:
        return f"Pre-check GAQL failed: {err}"
    if not rows:
        return f"No campaign found with id {cid_digits} in customer {formatted_customer_id}."

    row0 = rows[0]
    ch = (row0.get("campaign") or {}).get("advertisingChannelType", "")
    if not allow_non_search and ch != "SEARCH":
        return (
            f"Refusing mutate: campaign {cid_digits} has advertising_channel_type={ch!r}, "
            f"expected SEARCH. Pass allow_non_search=True to override."
        )

    resource_name = f"customers/{formatted_customer_id}/campaigns/{cid_digits}"
    update_obj: Dict[str, Any] = {"resourceName": resource_name}
    mask_parts: List[str] = []

    if name is not None:
        update_obj["name"] = str(name)
        mask_parts.append("name")
    if status is not None:
        update_obj["status"] = str(status).upper()
        mask_parts.append("status")

    body = {
        "operations": [{"updateMask": ",".join(mask_parts), "update": update_obj}],
        "validateOnly": bool(validate_only),
        "partialFailure": False,
    }

    url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/campaigns:mutate"
    data, m_err = _post_with_login_retry(url, body)
    if m_err:
        return f"Mutate failed: {m_err}"
    return _format_mutate_response(
        f"campaigns:mutate ({'validate_only' if validate_only else 'applied'}) "
        f"customer={formatted_customer_id} campaign={cid_digits}",
        data or {},
    )


@mcp.tool()
async def update_search_campaign_budget_micros(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    campaign_id: str = Field(
        description="Numeric campaign.id whose **linked campaign budget** should be updated"
    ),
    amount_micros: int = Field(
        description="New daily budget in **account currency micros** (e.g. 5000000 = 5.00 INR/USD units)"
    ),
    validate_only: bool = Field(
        default=False,
        description="If True, validate the mutate but do not apply it.",
    ),
    allow_non_search: bool = Field(
        default=False,
        description="If False (default), refuse when the campaign is not SEARCH.",
    ),
) -> str:
    """
    Set the **daily budget amount** for the **CampaignBudget** linked to a **Search** campaign.

    Resolves ``campaign.campaign_budget`` via GAQL, then calls **campaignBudgets:mutate** with
    ``updateMask: amountMicros``. Same env guards as ``update_search_campaign``.
    """
    if _mutations_disabled_by_env():
        return "Mutations are disabled (GOOGLE_ADS_DISABLE_MUTATIONS=1). Remove or unset to allow updates."

    if amount_micros <= 0:
        return "Error: amount_micros must be a positive integer."

    if _mutate_validate_only_forced():
        validate_only = True

    formatted_customer_id = format_customer_id(customer_id)
    camp_digits = "".join(ch for ch in str(campaign_id) if ch.isdigit())
    if not camp_digits:
        return "Error: campaign_id must contain digits."

    pre_query = f"""
        SELECT
            campaign.id,
            campaign.advertising_channel_type,
            campaign.campaign_budget
        FROM campaign
        WHERE campaign.id = {camp_digits}
        LIMIT 1
    """
    rows, err = _gaql_search_raw(formatted_customer_id, pre_query)
    if err:
        return f"Pre-check GAQL failed: {err}"
    if not rows:
        return f"No campaign found with id {camp_digits} in customer {formatted_customer_id}."

    camp = rows[0].get("campaign") or {}
    ch = camp.get("advertisingChannelType", "")
    if not allow_non_search and ch != "SEARCH":
        return (
            f"Refusing mutate: campaign {camp_digits} has advertising_channel_type={ch!r}, "
            f"expected SEARCH. Pass allow_non_search=True to override."
        )

    budget_rn = camp.get("campaignBudget")
    if not budget_rn:
        return "Campaign has no campaign_budget resource link; cannot update budget via this helper."

    body = {
        "operations": [
            {
                "updateMask": "amountMicros",
                "update": {"resourceName": budget_rn, "amountMicros": int(amount_micros)},
            }
        ],
        "validateOnly": bool(validate_only),
        "partialFailure": False,
    }

    url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/campaignBudgets:mutate"
    data, m_err = _post_with_login_retry(url, body)
    if m_err:
        return f"Mutate failed: {m_err}"
    return _format_mutate_response(
        f"campaignBudgets:mutate ({'validate_only' if validate_only else 'applied'}) "
        f"customer={formatted_customer_id} campaign={camp_digits} budget={budget_rn}",
        data or {},
    )


# Register Supabase memory / reporting tools (optional; requires SUPABASE_* env vars)
import memory_tools  # noqa: E402, F401
import analysis_tools  # noqa: E402, F401
import keyword_plan_tools  # noqa: E402, F401

if __name__ == "__main__":
    # Start the MCP server on stdio transport
    mcp.run(transport="stdio")
