# EyeRIS products subdomain — landing page analysis (Exa)

**Analyzed:** 2026-05-25  
**Method:** Content extracted with Exa `web_fetch_exa` (full-page markdown) and `web_search_exa` (`site:products.eyerisinteractive.com`). This reflects what crawlers and AI readers can see; it is not a substitute for Lighthouse, Core Web Vitals, or accessibility tooling.

## Scope

| URL | Role |
| --- | --- |
| [https://products.eyerisinteractive.com/maharashtra/](https://products.eyerisinteractive.com/maharashtra/) | State/geo landing (primary focus) |
| [https://products.eyerisinteractive.com/](https://products.eyerisinteractive.com/) | Global product homepage |

Other indexed paths on the same host are listed under [Related pages](#related-pages).

---

## Maharashtra page — summary

**Positioning:** “India’s First Agentic Interactive Flat Panel (AIFP™)” and “Powering leading institutions across Maharashtra,” with **Cybernetyx AIFP™** and **BrightClass** called out as the product stack. Trust line: **1,500+ schools** in India using the solution.

**Audience:** Schools and colleges in Maharashtra; forms also capture tutors, dealers, and “other.”

**Information architecture (as rendered in text):**

1. Hero + primary CTAs: **Book a Demo**, **Download Brochure**
2. Product tiles: **A10 Pro** (“True AI Digital Board”), **A20 EDLA** (“Google Certified AI Digital Board”) — each with brochure CTA
3. Problem/solution: teachers juggling tools → unified teaching, content, assessment, AI (“built for institutions across Maharashtra”)
4. Feature grid: 3D + AI copilot, virtual lab, mind mapping, slide generator, fiducial polling, curriculum alignment (CBSE/ICSE/state), offline AI, admin broadcast, reporting
5. **Maharashtra-specific** social proof: quotes attributed to DY Patil Vidyapeeth, Vidya Pratishthan, Indus International
6. Closing CTA + lead forms: demo/pricing wizard (category, budget bands in ₹), brochure capture (name, phone, email, category, budget)
7. Global stats strip: 80+ countries, 15M+ users, 10K+ organisations

**Conversion mechanics:** Multi-step demo request, budget segmentation (₹70k–1L / 1–1.5L / 1.5L+), scarcity copy (“only 5 Demo Slots left this week”), duplicate brochure blocks in the extracted content (may indicate repeated sections or above-the-fold + footer modules).

---

## Root homepage (`/`) — how it differs

| Dimension | Root `/` | Maharashtra `/maharashtra/` |
| --- | --- | --- |
| Headline | “#1 AI Digital Board…” / future-of-education framing | AIFP™ + **state** leadership |
| Social proof scale | **15,000+** institutions | **1,500+** schools (national, used in state context) |
| Product naming | **EyeRIS**-centric copy | **Cybernetyx AIFP™** + EyeRIS/BrightClass |
| AI capabilities | Broader list (lesson/quiz/PPT, image/video, Chat-with-PDF, auto-draw, circle-to-search, math/chemistry recognition) | Subset focused on classroom delivery + admin |

**Copy quality (root):** The fetched markdown shows a typo (“featuresthe”) and **duplicated** hero/AI sections — likely template or CMS duplication; worth fixing for credibility and SEO.

---

## Cross-page consistency risks (from Exa snippets)

Exa’s indexed highlights for **other** geo pages suggest **template leakage** (same body copy as Maharashtra):

- **Gujarat:** One paragraph still refers to institutions “across **Maharashtra**.”
- **Bangalore / Karnataka:** Testimonials or headings reference “**Maharashtra**” where “Karnataka” would be expected; closing CTA text may still say “Join Schools in **Maharashtra**.”

These errors hurt local SEO and trust. A pass that parameterizes region names in shared modules is recommended.

---

## Messaging and SEO notes

**Strengths**

- Clear hierarchy: hero → products → proof → forms
- State page uses **local proof** (named institutions), which supports geo intent
- Strong feature list aligned to Indian boards and connectivity constraints (offline AI)

**Gaps / improvements**

- Reconcile **1,500+** vs **15,000+** institution counts (footnote both if they measure different entities, e.g. Cybernetyx vs EyeRIS group)
- Reduce **duplicate** long-form blocks on `/` and repeated brochure sections if not intentional
- Fix **region placeholders** on non-Maharashtra landings
- Unify **brand ladder** (EyeRIS vs Cybernetyx vs BrightClass) in one line early on each page

---

## Related pages

Discovered via `site:products.eyerisinteractive.com` (not exhaustive):

| Path | Topic |
| --- | --- |
| `/` | Global EyeRIS homepage |
| `/maharashtra/` | Maharashtra geo landing |
| `/gujarat/` | Gujarat geo landing |
| `/bangalore/` | Karnataka/Bangalore geo landing |
| `/electronic-whiteboard/` | Category/education whiteboard |
| `/eyeris-a10/` | Product SKU landing |
| `/smart-board-comparison/` | Comparison / competitive |
| `/tag/online-course/` | Blog/tag index (LMS / online course articles) |

Additional geo or product URLs may exist but were not returned in this search batch.

---

## Sources

- Fetched markdown: `https://products.eyerisinteractive.com/maharashtra/`, `https://products.eyerisinteractive.com/`
- Search: `site:products.eyerisinteractive.com` via Exa web search
