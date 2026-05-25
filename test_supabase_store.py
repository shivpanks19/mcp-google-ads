"""Tests for supabase_store (mocked Supabase client; no live DB)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import supabase_store as store


class TestConfiguration(unittest.TestCase):
    def setUp(self) -> None:
        store.reset_client()
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        store.reset_client()

    def test_is_configured_false_when_missing(self) -> None:
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        self.assertFalse(store.is_configured())

    def test_is_configured_true_when_set(self) -> None:
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"
        self.assertTrue(store.is_configured())

    def test_get_client_raises_when_not_configured(self) -> None:
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        with self.assertRaises(store.SupabaseNotConfiguredError):
            store.get_client()


class TestNormalizeCustomerId(unittest.TestCase):
    def test_strips_dashes(self) -> None:
        self.assertEqual(store.normalize_customer_id("123-456-7890"), "1234567890")

    def test_rejects_invalid_length(self) -> None:
        with self.assertRaises(ValueError):
            store.normalize_customer_id("12345")


class TestUpsertClient(unittest.TestCase):
    def setUp(self) -> None:
        store.reset_client()
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"

    def tearDown(self) -> None:
        store.reset_client()

    @patch("supabase_store.create_client")
    def test_insert_new_client(self, mock_create: MagicMock) -> None:
        sb = MagicMock()
        mock_create.return_value = sb

        new_row = {
            "id": "uuid-1",
            "customer_id": "1234567890",
            "descriptive_name": "Test Account",
            "aliases": ["alias"],
        }
        clients_table = MagicMock()
        clients_table.select.return_value = clients_table
        clients_table.eq.return_value = clients_table
        clients_table.limit.return_value = clients_table
        insert_chain = MagicMock()
        insert_chain.execute.return_value = MagicMock(data=[new_row])
        clients_table.insert.return_value = insert_chain
        clients_table.execute.return_value = MagicMock(data=[])
        sb.table.return_value = clients_table

        row = store.upsert_client(
            "123-456-7890",
            descriptive_name="Test Account",
            aliases=["alias"],
        )
        self.assertEqual(row["customer_id"], "1234567890")
        self.assertEqual(row["descriptive_name"], "Test Account")
        clients_table.insert.assert_called_once()


class TestInsertMemory(unittest.TestCase):
    def setUp(self) -> None:
        store.reset_client()
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"

    def tearDown(self) -> None:
        store.reset_client()

    @patch("supabase_store.create_client")
    def test_insert_memory_without_client(self, mock_create: MagicMock) -> None:
        sb = MagicMock()
        mock_create.return_value = sb
        mem_table = MagicMock()
        mem_table.insert.return_value = mem_table
        mem_table.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": "mem-1", "content": "Test insight", "entry_type": "insight"}]
        )
        sb.table.return_value = mem_table

        row = store.insert_memory(content="Test insight", entry_type="insight")
        self.assertEqual(row["content"], "Test insight")
        mem_table.insert.assert_called_once()

    @patch("supabase_store.get_client_by_customer_id")
    @patch("supabase_store.create_client")
    def test_insert_memory_requires_client_when_customer_id_set(
        self, mock_create: MagicMock, mock_get_client: MagicMock
    ) -> None:
        mock_get_client.return_value = None
        with self.assertRaises(ValueError):
            store.insert_memory(content="x", customer_id="1234567890")


class TestFindClients(unittest.TestCase):
    @patch("supabase_store.create_client")
    def test_find_by_alias(self, mock_create: MagicMock) -> None:
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"
        store.reset_client()

        sb = MagicMock()
        mock_create.return_value = sb
        clients_table = MagicMock()
        clients_table.select.return_value = clients_table
        clients_table.limit.return_value = clients_table
        clients_table.limit.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "u1",
                    "customer_id": "1111111111",
                    "descriptive_name": "Acme Corp",
                    "aliases": ["EyeRIS"],
                },
                {
                    "id": "u2",
                    "customer_id": "2222222222",
                    "descriptive_name": "Other",
                    "aliases": [],
                },
            ]
        )
        sb.table.return_value = clients_table

        matches = store.find_clients_by_query("eyeris")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["customer_id"], "1111111111")

        store.reset_client()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)


class TestParseListAccountsOutput(unittest.TestCase):
    def test_parse_ids(self) -> None:
        text = """Accessible Google Ads Accounts:
--------------------------------------------------
Account ID: 1234567890
Account ID: 0987654321
"""
        self.assertEqual(
            store.parse_account_ids_from_list_accounts_output(text),
            ["1234567890", "0987654321"],
        )

    def test_parse_empty(self) -> None:
        self.assertEqual(store.parse_account_ids_from_list_accounts_output(""), [])

    def test_sync_error_prefix_returns_dict(self) -> None:
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"
        store.reset_client()
        out = store.sync_list_accounts_output_to_clients("Error listing accounts: denied")
        self.assertEqual(out["status"], "error")
        self.assertIn("denied", out["message"])
        store.reset_client()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

    def test_sync_pgrst205_returns_schema_missing(self) -> None:
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"
        store.reset_client()
        listing = "Accessible Google Ads Accounts:\nAccount ID: 1234567890\n"
        exc = Exception(
            {
                "message": "Could not find the table 'public.google_ads_clients' in the schema cache",
                "code": "PGRST205",
            }
        )
        with patch("supabase_store.get_client_by_customer_id", side_effect=exc):
            out = store.sync_list_accounts_output_to_clients(listing)
        self.assertEqual(out["status"], "supabase_schema_missing")
        self.assertIn("001_initial", out["message"])
        self.assertEqual(out["customer_ids"], ["1234567890"])
        self.assertEqual(out["clients"], [])
        store.reset_client()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

    def test_sync_rls_error_returns_permission_denied_not_schema(self) -> None:
        """RLS messages mention the table name; must not be classified as schema missing."""
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"
        store.reset_client()
        listing = "Accessible Google Ads Accounts:\nAccount ID: 1234567890\n"
        exc = Exception(
            {
                "message": 'new row violates row-level security policy for table "google_ads_clients"',
                "code": "42501",
            }
        )
        with patch("supabase_store.get_client_by_customer_id", side_effect=exc):
            out = store.sync_list_accounts_output_to_clients(listing)
        self.assertEqual(out["status"], "supabase_permission_denied")
        self.assertIn("service_role", out["message"])
        self.assertEqual(out["customer_ids"], ["1234567890"])
        store.reset_client()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)


class TestPersistCampaignPerformanceSnapshot(unittest.TestCase):
    def test_not_configured(self) -> None:
        store.reset_client()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        out = store.persist_campaign_performance_snapshot(
            "1234567890",
            7,
            [{"campaign": {"id": "1"}}],
        )
        self.assertEqual(out["status"], "skipped")
        self.assertIn("not set", out["message"])

    @patch("supabase_store.insert_report_snapshot")
    @patch("supabase_store.get_client_by_customer_id")
    def test_inserts_when_client_exists(
        self, mock_get: MagicMock, mock_insert: MagicMock
    ) -> None:
        store.reset_client()
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"
        mock_get.return_value = {"id": "cid-1", "customer_id": "1234567890"}
        mock_insert.return_value = {
            "id": "snap-1",
            "client_id": "cid-1",
            "report_type": "campaign_performance_last_7d",
        }
        out = store.persist_campaign_performance_snapshot(
            "1234567890",
            7,
            [{"x": 1}],
            summary="weekly",
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["report_type"], "campaign_performance_last_7d")
        mock_insert.assert_called_once()
        call_kw = mock_insert.call_args.kwargs
        self.assertEqual(call_kw["summary"], "weekly")
        self.assertEqual(call_kw["metrics"]["row_count"], 1)
        store.reset_client()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

    @patch("supabase_store.insert_report_snapshot")
    @patch("supabase_store.upsert_client")
    @patch("supabase_store.get_client_by_customer_id")
    def test_upserts_client_when_missing(
        self,
        mock_get: MagicMock,
        mock_upsert: MagicMock,
        mock_insert: MagicMock,
    ) -> None:
        store.reset_client()
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"
        mock_get.return_value = None
        mock_upsert.return_value = {"id": "cid-new", "customer_id": "1234567890"}
        mock_insert.return_value = {"id": "snap-2", "report_type": "campaign_performance_last_30d"}
        out = store.persist_campaign_performance_snapshot("1234567890", 30, [])
        self.assertEqual(out["status"], "ok")
        mock_upsert.assert_called_once()
        mock_insert.assert_called_once()
        store.reset_client()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)


class TestAnalysisTextSnapshots(unittest.TestCase):
    @patch("supabase_store.get_client")
    @patch("supabase_store.get_client_by_customer_id")
    def test_insert_analysis_snapshot(
        self, mock_get_row: MagicMock, mock_get_sb: MagicMock
    ) -> None:
        store.reset_client()
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"
        mock_get_row.return_value = {"id": "cid-1", "customer_id": "1234567890"}
        sb = MagicMock()
        mock_get_sb.return_value = sb
        insert_chain = MagicMock()
        insert_chain.execute.return_value = MagicMock(
            data=[
                {
                    "id": "ax-1",
                    "client_id": "cid-1",
                    "analysis_type": "campaign_performance_analysis",
                    "title": "Week vs month",
                    "body": "Analysis text",
                    "metadata": {"currency": "INR"},
                    "created_at": "2026-05-19T12:00:00+00:00",
                }
            ]
        )
        tbl = MagicMock()
        tbl.insert.return_value = insert_chain
        sb.table.return_value = tbl

        row = store.insert_analysis_text_snapshot(
            "1234567890",
            "campaign_performance_analysis",
            "Analysis text",
            title="Week vs month",
            metadata={"currency": "INR"},
            auto_upsert_client=False,
        )
        self.assertEqual(row["id"], "ax-1")
        self.assertEqual(row["analysis_type"], "campaign_performance_analysis")
        sb.table.assert_called_with("analysis_text_snapshots")
        insert_chain.execute.assert_called_once()
        store.reset_client()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

    def test_list_analysis_no_client(self) -> None:
        store.reset_client()
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"
        with patch("supabase_store.get_client_by_customer_id", return_value=None):
            self.assertEqual(store.list_analysis_text_snapshots("1234567890"), [])
        store.reset_client()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

    @patch("supabase_store.get_client")
    @patch("supabase_store.get_client_by_customer_id")
    def test_list_analysis_returns_rows(
        self, mock_get_row: MagicMock, mock_get_sb: MagicMock
    ) -> None:
        store.reset_client()
        os.environ["SUPABASE_URL"] = "https://test.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "secret"
        mock_get_row.return_value = {"id": "cid-1"}
        sb = MagicMock()
        mock_get_sb.return_value = sb
        q = MagicMock()
        sb.table.return_value = q
        q.select.return_value = q
        q.eq.return_value = q
        q.gte.return_value = q
        q.lte.return_value = q
        q.order.return_value = q
        final = MagicMock()
        q.limit.return_value = final
        final.execute.return_value = MagicMock(
            data=[{"id": "r1", "body": "x", "analysis_type": "ad_performance_analysis"}]
        )

        rows = store.list_analysis_text_snapshots(
            "1234567890",
            analysis_type="ad_performance_analysis",
            since="2026-05-01",
            limit=5,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["analysis_type"], "ad_performance_analysis")
        q.gte.assert_called_once()
        q.order.assert_called_once_with("created_at", desc=True)
        q.limit.assert_called_once_with(5)
        final.execute.assert_called_once()
        store.reset_client()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)


if __name__ == "__main__":
    unittest.main()
