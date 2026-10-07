import pandas as pd
import re
import os

print("Loading data...")
file_path = 'Google Review.xlsx'
df = pd.read_excel(file_path)

store_col = df.columns[0]
df['Rating'] = pd.to_numeric(df['Rating'], errors='coerce')
df = df[df['Rating'].isin([1, 2])]
df['Comment'] = df['Comment'].fillna('').astype(str)

# Filter valid reviews (non-empty)
df = df[df['Comment'].str.strip().str.len() > 0]

print(f"Total 1 and 2 star reviews to process: {len(df)}")

# Define RCA mapping exactly as requested
rca_mapping = {
    "After-Sales & Service Friction": {
        "Severe Delays & Turnaround Lag": r"(?i)(delay|wait|lag|take long|weeks|months|too much time|slow|delivery|stock|available|deliver)",
        "Warranty & Replacement Disputes": r"(?i)(warranty|replace|replacement|guarantee|claim)",
        "Unresolved Faults / Defective Repair": r"(?i)(not working|defective|fault|damage|repair|fix|not fixed|issue remain|problem|low quality|worst product)",
        "Missing Updates & Proactive Follow-up": r"(?i)(update|status|inform|intimate)",
        "Service Charges & Spare Cost Disputes": r"(?i)(charge|cost|spare|money|pay|price|bill|expensive|loot)",
        "General After-Sales Grievances": r"(?i)(service center|after sale|after service|service network|service|mosham|shokam|bad experience|worst experience)"
    },
    "Staff Interaction & Attitude": {
        "Unprofessionalism & Knowledge Gaps": r"(?i)(unprofessional|knowledge|uneducated|untrained|no idea|clueless|useless|waste)",
        "Rude & Arrogant Behavior / Disrespect": r"(?i)(rude|arrogant|disrespect|misbehave|behaviou?r|insult|ego|bad words|dealing|handling)",
        "Inattention & Walk-in Neglect": r"(?i)(ignore|nobody|care|attend|standing|waiting for someone|not attending|support|customer service)",
        "Poor Attitude & Budget Bias": r"(?i)(attitude|budget|cheap|judging|judge|offer|fraud|thattippu|fake)",
        "General Staff Dissatisfaction": r"(?i)(staff|employee|salesperson|salesman|executive|manager)"
    },
    "Communication Breakdown": {
        "Customer Follow-up Fatigue (Chasing)": r"(?i)(follow up|following up|chase|multiple times|again and again|tired of calling)",
        "Broken Promises & No Callbacks": r"(?i)(promise|callback|call back|never called|will call)",
        "Rude Telephonic Response & Tone": r"(?i)(tone|phone rude|speak properly)",
        "Unanswered Calls, Busy & Disconnected": r"(?i)(unanswered|lift|pick up|attend call|disconnect|busy|cut the call|not picking|no answer)",
        "General Zero-Response (Phone/WhatsApp)": r"(?i)(no response|reply|whatsapp|contact|customer care)"
    }
}

def classify_rca(text):
    results = {}
    for main_cat in rca_mapping.keys():
        results[main_cat] = 0
        for sub_cat in rca_mapping[main_cat].keys():
            results[sub_cat] = 0
    results["Other / Unclassified"] = 0
    
    matches = []
    sub_matches = []
    
    for main_cat, sub_cats in rca_mapping.items():
        for sub_cat, pattern in sub_cats.items():
            if re.search(pattern, text):
                matches.append(main_cat)
                sub_matches.append(sub_cat)
                
    if len(matches) > 0:
        # Assign to first matching main category
        main_match = matches[0]
        results[main_match] = 1
        
        # Assign to first matching sub-category WITHIN that main category
        # to ensure mutually exclusive sub-categories that sum up to the main category
        for sub in sub_matches:
            if sub in rca_mapping[main_match]:
                results[sub] = 1
                break
    else:
        results["Other / Unclassified"] = 1
        
    return pd.Series(results)

print("Classifying reviews based on RCA matrix...")
rca_df = df['Comment'].apply(classify_rca)
df = pd.concat([df, rca_df], axis=1)

print("Aggregating by store...")
store_group = df.groupby(store_col)
summary_data = []

main_cats = list(rca_mapping.keys()) + ["Other / Unclassified"]
all_sub_cats = []
for subs in rca_mapping.values():
    all_sub_cats.extend(list(subs.keys()))

for store, grp in store_group:
    total_reviews = len(grp)
    if total_reviews == 0:
        continue
        
    row = {
        'Store Name': store,
        'Total 1★ & 2★ Reviews': total_reviews
    }
    
    for main_cat in main_cats:
        main_count = grp[main_cat].sum()
        row[f"{main_cat} (Count)"] = main_count
        row[f"{main_cat} (%)"] = f"{(main_count / total_reviews * 100):.1f}%" if total_reviews > 0 else "0.0%"
    
    for sub_cat in all_sub_cats:
        sub_count = grp[sub_cat].sum()
        row[f"{sub_cat} (Count)"] = sub_count
        # We don't necessarily need the percentage for the excel, the dashboard calculates it dynamically
        # but we'll include it anyway
        row[f"{sub_cat} (%)"] = f"{(sub_count / total_reviews * 100):.1f}%" if total_reviews > 0 else "0.0%"
            
    summary_data.append(row)

out_df = pd.DataFrame(summary_data)
out_df = out_df.sort_values(by='Total 1★ & 2★ Reviews', ascending=False)

out_path = 'Store_Level_RCA_Dashboard_Simplified_v3.xlsx'
out_df.to_excel(out_path, index=False, engine='openpyxl')
print(f"Done! Saved report to {out_path}")
