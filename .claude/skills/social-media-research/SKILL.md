---
name: social-media-research
description: Research public LinkedIn and Facebook company/profile pages and posts for evidence-based B2B prospect research. Use only public URLs and the ScrapeCreators API.
allowed-tools: Bash, Read, Write, WebFetch
version: 1.0.0
license: MIT
homepage: https://scrapecreators.com
metadata:
  requires:
    env:
      - SCRAPECREATORS_API_KEY
    primaryEnv: SCRAPECREATORS_API_KEY
---

# Social Media Research (LinkedIn + Facebook)

## Purpose
Use this skill to inspect public LinkedIn and Facebook company pages, profiles, and posts for B2B prospect research. This is an instruction skill; it is not itself a scraper executable or a broad search engine.

## Credentials and API
- Base URL: `https://api.scrapecreators.com`
- Authentication header: `x-api-key: $SCRAPECREATORS_API_KEY`
- Never print, expose, commit, or include the API key in reports.
- Before paid data requests, confirm the key is configured, check the current credit balance using the provider's documented credit-balance endpoint, estimate the request cost, and obtain user approval.
- Check the current endpoint OpenAPI specification at `https://docs.scrapecreators.com/{endpoint-path}/openapi.json` before relying on optional parameters or response fields.
- Do not assume requests are free. Respect account credits, provider limits, and platform terms.

## Supported public-page endpoints
Use only after validating the exact endpoint and URL format against current provider documentation:
- LinkedIn company: `/v1/linkedin/company?url={encoded_public_url}`
- LinkedIn profile: `/v1/linkedin/profile?url={encoded_public_url}`
- LinkedIn company posts: `/v1/linkedin/company/posts?url={encoded_public_url}`
- LinkedIn post: `/v1/linkedin/post?url={encoded_public_url}`
- Facebook profile/page: `/v1/facebook/profile?url={encoded_public_url}`
- Facebook posts: `/v1/facebook/profile/posts?url={encoded_public_url}`
- Facebook reels: `/v1/facebook/profile/reels?url={encoded_public_url}`
- Facebook post: `/v1/facebook/post?url={encoded_public_url}`
- Facebook post comments: `/v1/facebook/post/comments?url={encoded_public_url}`

These endpoints fetch known public URLs. They do not guarantee discovery of buyers from a product-and-country query.

## Research workflow
1. Identify the product category and target country.
2. If public profile/page URLs are missing, ask for them or use an explicitly authorized public discovery source; do not pretend a profile-fetch endpoint performs broad discovery.
3. Resolve ambiguous company names before making API calls.
4. If a connected lead store exists, read it first and exclude previously collected canonical URLs before new requests.
5. Check API credits and obtain approval before any credit-consuming request.
6. Make the minimum necessary calls. Use public company pages first; inspect posts only when they add meaningful evidence.
7. Preserve each source URL, retrieval timestamp, and post date. Normalize URLs and deduplicate records.
8. Separate observed facts from analysis. Mark missing information as unknown; never invent contacts, buyer status, location, or purchasing activity.

## B2B qualification
Assess:
- Product/category relevance.
- Evidence that the company operates in the target country.
- Public evidence of importing, distribution, wholesale, procurement, retail, or relevant manufacturing.
- Recent, relevant business activity.
- Publicly listed business contact channels.

Do not reject a potentially relevant lead solely because an optional contact field is absent. Explain uncertainty and evidence gaps.

## Output fields
For each candidate, return:
- Company/person name and platform.
- Canonical public profile/page URL.
- Public description and category.
- Location evidence and target-country match.
- Product relevance and supporting evidence.
- Public business contact details, only if explicitly listed.
- Relevant recent activity with dates and source URLs.
- Buyer-fit rationale, confidence, and retrieval timestamp.

## Safety and compliance
- Access public content only. Do not bypass logins, CAPTCHAs, private groups, or access controls.
- Do not collect private personal contact information or infer sensitive traits.
- Respect provider rate limits, platform terms, and applicable privacy laws.
- Keep secrets out of source control, artifacts, and logs.
