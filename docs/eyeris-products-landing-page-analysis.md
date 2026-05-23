# EyeRIS products subdomain: landing page analysis

**Scope:** [https://products.eyerisinteractive.com/maharashtra/](https://products.eyerisinteractive.com/maharashtra/) and sibling pages under [https://products.eyerisinteractive.com/](https://products.eyerisinteractive.com/).

**Method:** Public page text was retrieved with the Exa MCP `web_fetch_exa` tool (markdown extraction) and supporting URL discovery with `web_search_exa` (`inurl:products.eyerisinteractive.com`). **Snapshot date:** 2026-05-23. Crawl errors (for example `CRAWL_NOT_FOUND`) indicate a URL was not returned by the fetcher, not necessarily that the site has no page.

---

## Executive summary

The **Maharashtra** URL is a **geo-personalized variant** of the national EyeRIS / Cybernetyx AIFP story: state-specific hero copy, local social proof, and the same product pillars (A10 Pro, A20 EDLA, BrightClass-powered features) and lead funnels as the root `products` site. Messaging is strong for Indian K–12 and institutional buyers (curriculum alignment, offline AI, device management).

**High-impact content QA issue:** the **Bangalore / Karnataka** landing page still contains **Maharashtra** wording in the success-story intro and closing CTA block—likely a copy-paste error from the Maharashtra template. There are also minor typography issues on that page.

**Site structure:** Beyond state paths, the subdomain hosts **topic URLs** (electronic whiteboard, product line, comparisons), suggesting a programmatic or templated SEO/content hub aligned with paid and organic funnels.

---

## Maharashtra page ([/maharashtra/](https://products.eyerisinteractive.com/maharashtra/))

### Positioning and narrative

- **Hero framing:** “India’s First Agentic Interactive Flat Panel (AIFP™)” and “Powering leading institutions across **Maharashtra**.”
- **Trust line:** “Trusted by **1,500+** schools” with Cybernetyx AIFP™ and BrightClass—tighter local proof than the root site’s “15,000+ Institutions.”
- **Value proposition:** Teacher-first, curriculum-trained AI; unified teaching, content, assessment, and AI assistance; built for “India’s real classrooms.”

### Funnel and conversion elements

- **Primary CTAs:** Book a Demo, Download Brochure (repeated for product modules).
- **Mid-page CTA:** “Check the Demo” after the teacher-pain section.
- **Lead form:** Category (school / tutor / dealer / other), **budget bands in INR** (₹70k–1L, 1–1.5L, 1.5L+), and scarcity copy (“only 5 Demo Slots left this week”).
- **Secondary capture:** Duplicate “Get the brochure instantly on your Mail / Whatsapp” blocks with full fields (name, phone, email, category, budget).

### Feature story (consistent with national product pages)

- **Product lines:** A10 Pro (“True AI Digital Board”), A20 EDLA (“Google Certified AI Digital Board”).
- **Classroom features:** 3D tools with AI copilot, Lab in Board, mind mapping, slides generator, student polling with fiducial markers, built-in curriculum (CBSE, ICSE, state boards), offline AI.
- **Admin:** Broadcast messages to panels, reporting (“because of EyeRIS AI Smart Board” phrasing is awkward but readable).

### Social proof

- Three named testimonials with roles and institutions (DY Patil Vidyapeeth, Vidya Pratishthan, Indus International)—all **Maharashtra-relevant** names, which supports the local page intent.

### Strengths

- Clear **state + product + AI** SEO intent and on-page relevance.
- **Concrete Indian context:** boards, rupee pricing, connectivity/offline story.
- **Single-thread narrative** from problem → product → features → proof → CTA.

### Weaknesses / risks

- **Duplicate brochure sections** at the bottom increase noise and may split attribution in analytics unless forms are identical instances of one component.
- **Brand stack:** EyeRIS Interactive + Cybernetyx AIFP™ + BrightClass—works for insiders but may need a one-line legend for first-time buyers.
- Footer-style stats (**80+ countries, 15mn+ users, 10K+ organisations**) repeat patterns from the main marketing site; ensure they stay consistent with legal/compliance-approved figures across domains.

---

## Root and sibling pages on `products.eyerisinteractive.com`

### Root ([/](https://products.eyerisinteractive.com/))

- **Positioning:** “#1 AI Digital Board Built for Future-Ready Classrooms,” “15,000+ Institutions,” India-focused classroom copy.
- **Deeper AI feature list** than Maharashtra: AI lesson/quiz/PPT generation, image/video generation, Chat with PDF, auto draw, circle-to-search, math and chemistry recognition—suggests the root URL is the **maximal** feature narrative; state pages are **shortened + localized** variants.
- **Quality issues visible in extracted text:** duplicated H2 blocks (“The AI Digital Board from the Future of Education” repeated), typos/spacing (“featuresthe,” “classrooms.Join”), and in the “AI That Works Like a Teaching Assistant” area the **AI Quiz Generation** blurb appears to **duplicate the AI Lesson Creation** text—likely a CMS/template error worth fixing for credibility.

### Karnataka / Bangalore ([/bangalore/](https://products.eyerisinteractive.com/bangalore/))

- **Correct local hero:** “Karnataka’s No. #1 Choice…”, “Powering leading institutions across Karnataka.”
- **Definite bugs (fix before paid geo traffic):**
  1. Heading typo: **“Karnataka'sTop”** missing space after the possessive.
  2. Success stories intro: “educators across **Maharashtra** have to say” should be **Karnataka**.
  3. Closing section title: “Join Schools in **Maharashtra** Already Using Cybernetyx AIFP™” should be **Karnataka** (or city-specific copy).

### URLs that did not return in fetch (sample)

- `https://products.eyerisinteractive.com/delhi/` → **CRAWL_NOT_FOUND**
- `https://products.eyerisinteractive.com/karnataka/` → **CRAWL_NOT_FOUND**

State naming may use **city slugs** (Bangalore) rather than state slugs (Karnataka); consider **301 redirects** from predictable alternatives to the canonical page to reduce 404s and campaign broken links.

### Other discovered paths (from search highlights; non-exhaustive)

| Path | Role (inferred) |
|------|-----------------|
| `/electronic-whiteboard/` | Category / intent page |
| `/eyeris-a10/` | Product SKU page |
| `/digital-board-comparison/` | Competitive comparison / mid-funnel |
| `/smart-board-comparison/` | Same, alternate keyword |

---

## Cross-cutting recommendations

1. **Template QA:** Add automated checks (or a simple spreadsheet) for **geo tokens** in headings and body so Karnataka pages cannot ship with Maharashtra strings.
2. **Canonical and hreflang:** If multiple local URLs exist, ensure **canonical** tags and internal linking from root → regional hubs are explicit for SEO.
3. **Dedupe forms:** Two identical brochure forms on the same URL may hurt UX; consider one sticky CTA + one full form.
4. **Copy consistency:** Align institution counts (1,500+ vs 15,000+) with a footnote (“in Maharashtra” vs “nationally”) to avoid perceived contradiction.
5. **404 strategy:** Publish a lightweight **sitemap or directory page** listing valid regional slugs, or redirect common mistakes (`/karnataka/` → `/bangalore/`).

---

## Sources

- Fetched markdown: `https://products.eyerisinteractive.com/maharashtra/`, `https://products.eyerisinteractive.com/`, `https://products.eyerisinteractive.com/bangalore/`
- Search-assisted URL inventory: `web_search_exa` with `inurl:products.eyerisinteractive.com EyeRIS Interactive landing`
