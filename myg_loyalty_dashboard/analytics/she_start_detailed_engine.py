import os
from collections import OrderedDict
import gspread
from django.conf import settings
from analytics.she_start_engine import _parse_score, _find_key, _get_excel_mapping

def fetch_she_start_detailed_data():
    try:
        service_account_path = os.path.join(settings.BASE_DIR.parent, 'project_folder', 'service_account.json')
        render_secret_path = '/etc/secrets/service_account.json'
        env_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
        
        if env_json:
            import json
            gc = gspread.service_account_from_dict(json.loads(env_json))
        elif os.path.exists(service_account_path):
            gc = gspread.service_account(filename=service_account_path)
        elif os.path.exists(render_secret_path):
            gc = gspread.service_account(filename=render_secret_path)
        else:
            return {"error": "Missing service_account.json. Cannot authenticate with Google Sheets."}
        
        sheet_id = '1qVW_WZx3yu5l-iRd6h0pe5BxMBDk3kDC4yQ3sFj4Q-o'
        try:
            sh = gc.open_by_key(sheet_id)
            worksheet = sh.get_worksheet_by_id(843019645)
        except gspread.exceptions.APIError as e:
            if '403' in str(e):
                return {"error": "Access Denied. Please share the Google Sheet with: loyalty-portal@theta-ocean-490106-f5.iam.gserviceaccount.com as a Viewer."}
            return {"error": f"Google Sheets API Error: {str(e)}"}
            
        raw_records = worksheet.get_all_records()
        
        if not raw_records:
            return {"candidates": []}
            
        evaluations_by_candidate = OrderedDict()
        
        for idx, row in enumerate(raw_records):
            r_lower = {k.lower().strip(): v for k, v in row.items()}
            
            candidate_name = _find_key(r_lower, ['name', 'full name', 'applicant name', 'candidate name'], f'Candidate {idx+1}').strip()
            panelist_name = _find_key(r_lower, ['panalist', 'panelist'], f'Panelist {idx+1}').strip()
            
            # Calculate the total points for this panelist by summing all 10 questions
            passion = _parse_score(r_lower, ['passion', 'commitment'])
            clarity = _parse_score(r_lower, ['clarity'])
            comm = _parse_score(r_lower, ['communication', 'presentation'])
            growth_raw = _parse_score(r_lower, ['growth potential', 'growth'])
            need_raw = _parse_score(r_lower, ['need for support', 'need'])
            social = _parse_score(r_lower, ['social', 'family impact'])
            inspirational = _parse_score(r_lower, ['inspirational value', 'inspirational'])
            innov = _parse_score(r_lower, ['innovation', 'uniqueness'])
            fin = _parse_score(r_lower, ['financial responsibility', 'financial'])
            utilization_raw = _parse_score(r_lower, ['utilization plan', 'utilization'])
            
            total_score = passion + clarity + comm + growth_raw + need_raw + social + inspirational + innov + fin + utilization_raw
            
            evaluation = {
                "panelist": panelist_name,
                "score": total_score
            }
            
            if candidate_name not in evaluations_by_candidate:
                evaluations_by_candidate[candidate_name] = []
            evaluations_by_candidate[candidate_name].append(evaluation)

        candidates = []
        excel_mapping = _get_excel_mapping()
        
        for idx, (candidate_name, evals) in enumerate(evaluations_by_candidate.items()):
            c_name_key = candidate_name.lower().strip()
            place = 'N/A'
            business_name = 'N/A'
            if c_name_key in excel_mapping:
                mapped = excel_mapping[c_name_key]
                place = mapped.get('district', 'N/A')
                business_name = mapped.get('business_name', 'N/A')
            
            # Sort evaluations by score to find high and low
            sorted_evals = sorted(evals, key=lambda x: x['score'])
            
            # Process panelist scores
            panelist_scores = []
            green_scores = []
            
            if len(sorted_evals) > 2:
                # Identify the single lowest and single highest score
                # Note: if there are 6 panelists, dropping 1 lowest and 1 highest leaves 4.
                # If there are duplicates (e.g. two 85s), we just drop one of them.
                lowest_idx = 0
                highest_idx = len(sorted_evals) - 1
                
                for i, ev in enumerate(sorted_evals):
                    status = 'green'
                    if i == lowest_idx:
                        status = 'red_low'
                    elif i == highest_idx:
                        status = 'red_high'
                    else:
                        green_scores.append(ev['score'])
                        
                    panelist_scores.append({
                        "panelist": ev["panelist"],
                        "score": ev["score"],
                        "status": status
                    })
            else:
                # If 2 or fewer panelists, just keep them as green (can't drop high/low safely)
                for ev in sorted_evals:
                    green_scores.append(ev['score'])
                    panelist_scores.append({
                        "panelist": ev["panelist"],
                        "score": ev["score"],
                        "status": 'green'
                    })
            
            final_average = round(sum(green_scores) / len(green_scores), 2) if green_scores else 0
            
            candidates.append({
                "sl_no": idx + 1,
                "candidate_name": candidate_name,
                "place": place,
                "business_name": business_name,
                "panelist_scores": panelist_scores,
                "final_average": final_average
            })
            
        return {"candidates": candidates}
        
    except Exception as e:
        import traceback
        return {"error": f"Unexpected error: {str(e)}", "trace": traceback.format_exc()}
