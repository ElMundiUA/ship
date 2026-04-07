# Procurement FAQ

**Purpose:** anticipate **commercial and legal** questions without pretending to be legal advice.  
**Audience:** procurement, vendor management, security reviewers.

## What is being “sold” or shared?

This repository documentation describes an **internal automation architecture** (reference implementation). It is **not** a separate commercial “docs product” unless your organisation packages it as such. Copyright and redistribution terms: [Legal & copyright](../legal-copyright.md).

## Open source vs services

- **Code & config** licensing is **path-specific**: the published **Ship** package is **[ElMundiUA/ship](https://github.com/ElMundiUA/ship)** (Apache 2.0 at root unless a file says otherwise). Inside **ElMundiUA/elmundi**, the parallel path `tools/linear-agent/` is the mirror; the rest of that monorepo is not automatically under the same terms (see [Legal & copyright](../legal-copyright.md)).
- **Hosted services** (GitHub, Linear, Cursor, optional Snyk/hosting) are **third-party subscriptions** with their own contracts and DPAs.

## Intellectual property

- **Prompts and skills** in-repo are part of the implementation; clarify ownership with your employer/legal counsel before external redistribution.
- **Customer-facing PDFs** should strip org-specific hostnames, keys, and trademarks if not approved for release.

## Data protection / subprocessors

- Identify which vendors process **personal data** (if any) in your deployment — e.g. issue text, emails, analytics.
- Complete **DPA / SCC** steps with each vendor as required; use [Security brief](security-brief.md) as a starting checklist.

## Support & SLA

- This documentation does not imply a **support SLA** unless your organisation offers one. Internal operators should use [Troubleshooting](TROUBLESHOOTING.md) and runbooks.

## Exit plan

- Orchestration is **GitHub Actions + Node**; issue provider and agent vendor are **adapter-shaped** (see [Vision & extensibility](enterprise.md)). Migration options: [Cursor Automations migration](CURSOR-AUTOMATIONS-MIGRATION.md).

## Who is the “data controller”?

Depends on your deployment and jurisdiction — **not specified here**. Work with privacy/legal for controller/processor roles across GitHub, Linear, Cursor, and your org.
