# Narrative Drug-Reaction Parser

Parses a clinical narrative to identify which drug caused which reaction using MedDRA LLT→PT mapping.

## Input Files

### `drugs.txt`
One drug name per line (or comma-separated):
```
amoxicillin
ibuprofen
```

### `reactions.csv`
CSV with header row containing `llt_name` and `pt_name` columns:
```csv
llt_name,pt_name
skin rash,Rash
nausea,Nausea
```

## Usage

```bash
# Inline narrative
python parse_narrative.py "Patient took amoxicillin and developed a rash." --pretty

# From a file
python parse_narrative.py --file narrative.txt --pretty

# Custom input files
python parse_narrative.py "..." --drugs my_drugs.txt --reactions my_reactions.csv --pretty
```

## Output JSON

```json
{
  "narrative": "...",
  "drugs_found": ["amoxicillin", "ibuprofen"],
  "reactions": [
    {
      "llt_term":    "skin rash",
      "pt_term":     "Rash",
      "matched_text":"skin rash",
      "position":    42,
      "drug":        "amoxicillin",
      "negated":     false
    }
  ],
  "by_drug": {
    "amoxicillin": ["Rash", "Nausea"],
    "unattributed": ["Headache"]
  }
}
```

## Logic
- **Drug association**: nearest drug mentioned *before* each reaction in the text
- **LLT→PT mapping**: found LLT term is mapped to its canonical PT term for output
- **Negation detection**: reactions preceded by "no", "denies", "without", etc. are flagged `negated: true` and excluded from `by_drug`
- **No external dependencies** — pure Python stdlib only
