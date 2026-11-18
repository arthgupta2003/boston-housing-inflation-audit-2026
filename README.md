# Greater Boston housing inflation audit

GREATER BOSTON HOUSING: INFLATION AUDIT
Analysis date: November 18, 2026

Run with Python 3.10 or later:
  python -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/python analyze_housing.py --data-dir data --output-dir results

The script downloads both Massachusetts ACS 1-year housing files directly
from Census, the geographic composition files, and BLS annual CPI data.
It verifies household IDs, geography, and the published CPI annual averages.
An Internet connection is needed only for files absent from the data directory.
No Census API key or IPUMS account is required. No person file is needed.

Files:
  findings.txt        Detailed findings, methods, and limitations
  audit_results.txt   Plain-text results suitable for direct quotation
  comparison.csv      All main comparisons and sensitivity specifications
  broader.csv         Broader income groups and alternative denominators
  geography.csv       Every Massachusetts PUMA and its target-county membership
  results.json        Main results, source URLs, file sizes and SHA-256 hashes
  analyze_housing.py  Complete download and estimation code
  requirements.txt   Python dependencies used for the run

The five-county target is exact in 2024 but cannot be identified exactly in
2006 PUMS. The main approximation includes whole PUMAs with more than half
of their 2000 population in the target counties. This is an independently
chosen approximation, NOT a recovered author-supplied crosswalk or code.
The reported endpoints round correctly, but this is not a bit-for-bit
reproduction of the authors' undisclosed workflow.

The 80 ACS replicate weights give sampling uncertainty conditional on the
chosen geography. They do not account for geography error, CPI uncertainty,
measurement error, or changes in household composition.

The data are public-use Census microdata. Credit Census, BLS, IPUMS geography,
and the Boston Foundation/Boston Indicators report as described in findings.txt.


[Full findings](findings.txt) | [Auditable result lines](audit_results.txt) | [Offline reproducibility bundle](reproducibility.zip)
