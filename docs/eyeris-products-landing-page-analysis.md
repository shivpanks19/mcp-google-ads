# Landing page analysis: products.eyerisinteractive.com

**Method:** Content was retrieved with the Exa MCP `web_fetch_exa` tool (markdown extraction) and cross-checked against the site’s WordPress `wp-sitemap-posts-page-*.xml` page sitemaps (curl, 2026-05-24).

**Primary URL reviewed:** [https://products.eyerisinteractive.com/maharashtra/](https://products.eyerisinteractive.com/maharashtra/)

**Hub / national framing:** [https://products.eyerisinteractive.com/](https://products.eyerisinteractive.com/)

---

## 1. What the Maharashtra page is for

The Maharashtra URL is a **geo-targeted variant** of the EyeRIS / Cybernetyx education IFP (interactive flat panel) story. It repeats the core product narrative—AI classroom panel, curriculum alignment, offline AI, demos and brochure capture—but swaps **national** proof points for **Maharashtra-specific** copy and testimonials.

**Hero positioning (from extracted content):**

- Frames the product as **“India’s First Agentic Interactive Flat Panel (AIFP™)”** and ties adoption to **“leading institutions across Maharashtra.”**
- Lead trust line: **“Trusted by 1,500+ schools”** (Maharashtra page) versus the root products homepage headline cluster that references **“15,000+ Institutions”** on the national page—suggesting either different funnels, different counting definitions, or copy that has drifted between templates.

**Brand naming:** The Maharashtra page leans heavily on **“Cybernetyx AIFP™”** and **BrightClass**, while the root `products.eyerisinteractive.com` page centers **“EyeRIS”** as the customer-facing product name. Visitors may not immediately see that EyeRIS and Cybernetyx are the same ecosystem; a short **“EyeRIS by Cybernetyx”** (or equivalent) line would reduce cognitive load.

---

## 2. Page structure and conversion path

Roughly top-to-bottom, the Maharashtra landing follows a standard **B2B edtech** pattern:

| Section | Role |
|--------|------|
| Hero + primary CTAs | **Book a Demo**, **Download Brochure** |
| Product cards | **A10 Pro**, **A20 EDLA** with brochure CTAs |
| Problem / agitation | Teachers juggling too many tools; promise of one system |
| Feature grid | 3D + AI copilot, virtual lab, mind maps, slide generator, polling with fiducial markers, curriculum, offline AI |
| Admin / IT | Broadcast messages, reporting |
| Social proof | **Maharashtra** schools: DY Patil Vidyapeeth, Vidya Pratishthan, Indus International |
| Closing CTA | Join schools in Maharashtra; book demo |
| Lead forms | Demo + pricing wizard (category → budget bands in ₹) |
| Brochure capture | Duplicate-looking blocks for Mail/WhatsApp brochure request |
| Footer stats | 80+ countries, 15M+ users, 10K+ organisations |

**Conversion mechanics:** Multi-step demo booking (“only 5 Demo Slots left this week”) and **₹** budget buckets align with Indian institutional procurement. That is appropriate for the audience.

---

## 3. Content and copy quality notes

**Strengths**

- Clear **state-level social proof** (named institutions and roles) supports local relevance.
- Feature list maps well to **classroom and admin** buyers (teacher workflow + device management).
- **Offline AI** and **fiducial polling** are differentiated bullets versus generic “smart board” pages.

**Issues and risks**

- **Duplicated brochure section** appears twice in the extracted markdown (“Get the brochure instantly on your Mail /Whatsapp” repeated with the same fields). That may be intentional A/B placement or a template bug; worth verifying in the CMS—it can look like a rendering error and hurt trust.
- **Inconsistent metrics** (1,500+ schools on Maharashtra vs 15,000+ institutions on the root products page) should be reconciled or qualified (e.g. “Maharashtra deployments” vs “India-wide”).
- **“Processing your submission...”** appears in static content in the fetch; if visible to users before submit, it reads like a stuck form state.
- Minor polish: “featuresthe” typo on the root page (from national fetch); Maharashtra body copy is cleaner but shares boilerplate with other regions.

---

## 4. Comparison: Maharashtra vs root products homepage

| Dimension | [products.eyerisinteractive.com/](https://products.eyerisinteractive.com/) (national) | [products.eyerisinteractive.com/maharashtra/](https://products.eyerisinteractive.com/maharashtra/) |
|-----------|----------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| Primary brand voice | **EyeRIS** digital board, AI features enumerated in depth (lesson/quiz/PPT, Chat with PDF, Auto Draw, etc.) | **Cybernetyx AIFP™**, Maharashtra institutions, testimonials |
| Depth of AI feature list | **Longer** feature narrative (many subsections) | **Shorter** grid; different emphasis (3D, lab, mind map, slides, polling, curriculum, offline) |
| Social proof | “Join 15,000+ Institutions” | Named **Maharashtra** schools + “1,500+ schools” line |
| CTAs | Book a Demo, Request Price, Download Brochure | Book a Demo, Download Brochure, budget-based demo flow |

**Strategic read:** The subdomain acts as a **performance marketing / regional SEO** layer: leaner pages with geo hooks, while the root URL carries richer “AI digital board” education. The main refinement is **consistent numbers and parent brand** so remarketing and organic visitors do not see conflicting stories.

---

## 5. Other URLs under the same subdomain

From `https://products.eyerisinteractive.com/wp-sitemap-posts-page-*.xml`, **published WordPress pages** include national and regional landings plus product/comparison URLs, including:

**Regional / geo-style**

- [https://products.eyerisinteractive.com/andhra-pradesh/](https://products.eyerisinteractive.com/andhra-pradesh/)
- [https://products.eyerisinteractive.com/bangalore/](https://products.eyerisinteractive.com/bangalore/)
- [https://products.eyerisinteractive.com/gujarat/](https://products.eyerisinteractive.com/gujarat/)
- [https://products.eyerisinteractive.com/kerala/](https://products.eyerisinteractive.com/kerala/)
- [https://products.eyerisinteractive.com/maharashtra/](https://products.eyerisinteractive.com/maharashtra/)
- [https://products.eyerisinteractive.com/south-region/](https://products.eyerisinteractive.com/south-region/)
- [https://products.eyerisinteractive.com/tamil-nadu/](https://products.eyerisinteractive.com/tamil-nadu/)

**Product / topic pages (examples from sitemap)**

- [https://products.eyerisinteractive.com/digital-board/](https://products.eyerisinteractive.com/digital-board/)
- [https://products.eyerisinteractive.com/digital-classroom/](https://products.eyerisinteractive.com/digital-classroom/)
- [https://products.eyerisinteractive.com/smart-board/](https://products.eyerisinteractive.com/smart-board/)
- [https://products.eyerisinteractive.com/smart-classroom/](https://products.eyerisinteractive.com/smart-classroom/)
- [https://products.eyerisinteractive.com/eyeris-a10/](https://products.eyerisinteractive.com/eyeris-a10/)
- Comparison URLs such as `smart-board-comparison/`, `interactive-flat-panel-comparison/`, `digital-board-comparison/`
- [https://products.eyerisinteractive.com/thankyou-page/](https://products.eyerisinteractive.com/thankyou-page/)

These likely share the same template family as Maharashtra; the same recommendations (metric alignment, duplicate modules, brand bridge) probably apply across the set.

---

## 6. SEO and technical hints (lightweight)

- **WordPress** (`wp-json`, `wp-sitemap.xml`) implies strong use of **page sitemaps**—good for discovery; ensure each regional page has unique **title, H1, and opening paragraph** beyond swapping the state name (Exa-extracted text suggests substantial localization already for Maharashtra).
- Internal linking: the raw homepage HTML sample did not surface deep links to state pages in a quick href scrape; if states are **orphaned** except via ads, adding **footer or hub links** (“Explore by state”) would help crawl paths and users.
- **Canonical strategy:** Confirm whether regional pages canonicalize to the root or self-canonical; both are valid, but the choice should match whether you want **indexable local landings** (usually self-canonical + unique copy).

---

## 7. Recommended next edits (prioritized)

1. **Unify proof points** or add footnotes so “1,500+ schools” and “15,000+ institutions” do not contradict across templates.
2. **Clarify EyeRIS vs Cybernetyx** in one line in the hero or nav on all regional pages.
3. **Remove or fix duplicate brochure blocks** and any stray “Processing your submission...” static text.
4. **Hub page:** Consider a single `/india/` or `/locations/` index listing Andhra Pradesh, Bangalore, Gujarat, Kerala, Maharashtra, Tamil Nadu, South Region—improves UX and internal linking.
5. **Schema:** If not already present in HTML, `Product` / `Organization` / `LocalBusiness` (where applicable) JSON-LD can strengthen rich results for institutional buyers searching “AI smart board Maharashtra.”

---

## 8. Source log

- Exa `web_fetch_exa`: `https://products.eyerisinteractive.com/maharashtra/`, `https://products.eyerisinteractive.com/`
- HTTP: `wp-sitemap.xml` and `wp-sitemap-posts-page-{1,2,3}.xml` on the same host

This document is an external marketing-site review only; it does not reflect access to analytics, Search Console, or form submission backends.
