import requests, re

r = requests.get("https://api.fda.gov/drug/label.json",
    params={"search": 'openfda.brand_name:"lipitor"', "limit": 1})
label = r.json()["results"][0]

ar   = label.get("adverse_reactions", [""])[0].lower()
warn = label.get("warnings", [""])[0].lower()
warn2= label.get("warnings_and_precautions", [""])[0].lower()
full = ar + " " + warn + " " + warn2

print("'type 2 diabetes mellitus' found:", "type 2 diabetes mellitus" in full)
print("'diabetes mellitus' found:",        "diabetes mellitus" in full)
print("'diabetes' found:",                 "diabetes" in full)

print("\nContexts:")
for m in re.finditer(r".{50}diabetes.{50}", full):
    print(" >>", m.group())
