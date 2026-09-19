# Assignment dataset

Synthetic. All companies, plants, documents and figures are fictional and were created
for this exercise. Do not supplement with outside sources or prior knowledge — every
statement in your output should be traceable to what is in this folder.

## plants.csv — 52 plant records

| column | notes |
|---|---|
| plant_id | unique, e.g. PL-001 — use this when citing a register fact |
| plant_name, company, city, country, region | location and ownership |
| product | Ethylene, Propylene, HDPE, LDPE, LLDPE, PP |
| capacity_kta | thousand tonnes per annum. **May be blank** where not publicly confirmed |
| process_route | e.g. Steam Cracking (Naphtha), PDH, Gas Phase, Slurry Loop |
| startup_year | first production, or expected year if not yet operating |
| status | Operating, Under construction, Idled |
| complex_id | units sharing a complex_id are on the same integrated site |
| source_type | provenance of the record, including "Not publicly confirmed" |

A blank `capacity_kta` means the figure is not publicly available. It does not mean zero,
and it should not be estimated.

## documents/ — 24 documents

Markdown with YAML front matter (`source_type`, `company` or `publisher`, `date`,
`title`). Company press releases, industry association bulletins, and one government
agency note. Cite by filename.

Documents span September 2025 to August 2026. Some later documents update or supersede
earlier ones.

## prices.csv — 190 monthly price assessments

| column | notes |
|---|---|
| month | first day of the assessment month (YYYY-MM-01) |
| product | Naphtha, Ethylene, Propylene, HDPE, PP |
| region | assessment location, e.g. "Southeast Asia CFR" |
| price | USD per tonne. **May be blank** where not publicly confirmed |
| unit | USD/tonne throughout |
| basis | Spot or Contract |
| source_type | provenance, including "Not publicly confirmed" |

Coverage runs January 2024 to August 2026. Not every product has a value in every month.
A missing month means no assessment is available for that month; it does not mean the
price was zero or unchanged.

The `basis` column matters. Values assessed on different bases are not directly
comparable.

---

## Part B — real company filings (not included here)

Part B of the assignment uses **real, publicly filed company reports**, which you source
yourself from the companies' investor relations pages. They are not bundled in this pack.
See the assignment brief for which companies and which documents.

Keep the two datasets separate. The synthetic world in this folder and the real companies
in Part B must never appear in the same note.
