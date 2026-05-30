import os
import gspread
from django.conf import settings
from collections import OrderedDict

def get_she_start_data():
    """
    Connects to the Google Sheet provided by the user, fetches all rows,
    calculates the weighted scores, and formats the data for the frontend table.
    """
    try:
        # 1. Setup Authentication
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
        
        # 2. Open Sheet
        # The URL provided: https://docs.google.com/spreadsheets/d/1qVW_WZx3yu5l-iRd6h0pe5BxMBDk3kDC4yQ3sFj4Q-o/edit
        sheet_id = '1qVW_WZx3yu5l-iRd6h0pe5BxMBDk3kDC4yQ3sFj4Q-o'
        try:
            sh = gc.open_by_key(sheet_id)
            worksheet = sh.get_worksheet_by_id(843019645)
        except gspread.exceptions.APIError as e:
            if '403' in str(e):
                return {"error": "Access Denied. Please share the Google Sheet with: loyalty-portal@theta-ocean-490106-f5.iam.gserviceaccount.com as a Viewer."}
            return {"error": f"Google Sheets API Error: {str(e)}"}
            
        # 3. Fetch Data
        raw_records = worksheet.get_all_records()
        
        if not raw_records:
            return {"candidates": []}
            
        evaluations_by_candidate = OrderedDict()
        
        for idx, row in enumerate(raw_records):
            r_lower = {k.lower().strip(): v for k, v in row.items()}
            
            candidate_name = _find_key(r_lower, ['name', 'full name', 'applicant name', 'candidate name'], f'Candidate {idx+1}').strip()
            
            # Extract scores based on exact sheet columns
            passion = _parse_score(r_lower, ['passion', 'commitment'])
            clarity = _parse_score(r_lower, ['clarity'])
            comm = _parse_score(r_lower, ['communication', 'presentation'])
            interview_raw = (passion + clarity + comm) / 3 if (passion + clarity + comm) > 0 else 0
            
            growth_raw = _parse_score(r_lower, ['growth potential', 'growth'])
            need_raw = _parse_score(r_lower, ['need for support', 'need'])
            
            social = _parse_score(r_lower, ['social', 'family impact'])
            inspirational = _parse_score(r_lower, ['inspirational value', 'inspirational'])
            emotional_raw = (social + inspirational) / 2 if (social + inspirational) > 0 else social or inspirational
            
            innov = _parse_score(r_lower, ['innovation', 'uniqueness'])
            fin = _parse_score(r_lower, ['financial responsibility', 'financial'])
            sustainability_raw = (innov + fin) / 2 if (innov + fin) > 0 else innov or fin
            
            utilization_raw = _parse_score(r_lower, ['utilization plan', 'utilization'])
            
            max_score = max([interview_raw, growth_raw, need_raw, emotional_raw, sustainability_raw, utilization_raw] + [0])
            scale_factor = 10 if max_score > 0 and max_score <= 10 else 1
            
            interview = interview_raw * scale_factor
            growth = growth_raw * scale_factor
            need = need_raw * scale_factor
            emotional = emotional_raw * scale_factor
            sustainability = sustainability_raw * scale_factor
            utilization = utilization_raw * scale_factor
            
            weighted_score = (interview * 0.40) + (growth * 0.15) + (need * 0.15) + (emotional * 0.10) + (sustainability * 0.10) + (utilization * 0.10)
            weighted_score = round(weighted_score, 2)
            
            strengths = _find_key(r_lower, ['strengths', 'positive'], 'None specified')
            concerns = _find_key(r_lower, ['concerns', 'risks', 'weakness'], 'None specified')
            comments = _find_key(r_lower, ['panel comments', 'comments', 'remarks'], 'No comments')
            recommended_product = _find_key(r_lower, ['recommended product', 'support required', 'product'], 'N/A')
            
            manual_decision = _find_key(r_lower, ['final decision', 'decision', 'status', 'selection status'], '')
            
            evaluation = {
                "interview": interview,
                "growth": growth,
                "need": need,
                "emotional": emotional,
                "sustainability": sustainability,
                "utilization": utilization,
                "weighted_score": weighted_score,
                "strengths": strengths,
                "concerns": concerns,
                "comments": comments,
                "recommended_product": recommended_product,
                "manual_decision": manual_decision
            }
            
            if candidate_name not in evaluations_by_candidate:
                evaluations_by_candidate[candidate_name] = []
            evaluations_by_candidate[candidate_name].append(evaluation)

        candidates = []
        excel_mapping = _get_excel_mapping()
        
        for idx, (candidate_name, evals) in enumerate(evaluations_by_candidate.items()):
            num_all = len(evals)
            if num_all == 0:
                continue
                
            # These 5 columns are marked only ONE time.
            # We filter out the zeros (blanks) to correctly extract the single given mark.
            def get_single_mark(key):
                given_scores = [e[key] for e in evals if e[key] > 0]
                return sum(given_scores) / len(given_scores) if given_scores else 0
                
            avg_growth = get_single_mark("growth")
            avg_need = get_single_mark("need")
            avg_emotional = get_single_mark("emotional")
            avg_sustainability = get_single_mark("sustainability")
            avg_utilization = get_single_mark("utilization")
            
            # ONLY for the Interview score, drop the top and bottom score
            if num_all >= 3:
                sorted_interviews = sorted([e["interview"] for e in evals])
                valid_interviews = sorted_interviews[1:-1]
                avg_interview = sum(valid_interviews) / len(valid_interviews)
            else:
                avg_interview = sum(e["interview"] for e in evals) / num_all
                
            # 1. Fetch any manual overrides from the local database
            from analytics.models import SheStartCandidateScore
            try:
                override = SheStartCandidateScore.objects.get(candidate_name=candidate_name)
                # If we have an override for interview, use it, otherwise keep the calculated avg_interview
                if override.interview is not None:
                    avg_interview = override.interview
                avg_growth = override.growth if override.growth is not None else ''
                avg_need = override.need if override.need is not None else ''
                avg_emotional = override.emotional if override.emotional is not None else ''
                avg_sustainability = override.sustainability if override.sustainability is not None else ''
                avg_utilization = override.utilization if override.utilization is not None else ''
            except SheStartCandidateScore.DoesNotExist:
                # If no DB record exists, start blank for the 5 columns
                avg_growth = ''
                avg_need = ''
                avg_emotional = ''
                avg_sustainability = ''
                avg_utilization = ''

            # 2. Recalculate Final Score and Decision if ALL 5 columns have values
            if all(v != '' for v in [avg_growth, avg_need, avg_emotional, avg_sustainability, avg_utilization]):
                avg_weighted_score = (avg_interview * 0.40) + (avg_growth * 0.15) + (avg_need * 0.15) + \
                                     (avg_emotional * 0.10) + (avg_sustainability * 0.10) + (avg_utilization * 0.10)
                avg_weighted_score = round(avg_weighted_score, 2)
                
                if avg_weighted_score >= 85:
                    final_decision = 'Strong Final Selection'
                elif avg_weighted_score >= 75:
                    final_decision = 'Recommended for Top 10'
                elif avg_weighted_score >= 65:
                    final_decision = 'Waitlist Consideration'
                else:
                    final_decision = 'Not Recommended'
            else:
                avg_weighted_score = ''
                final_decision = ''
            
            all_strengths = " | ".join(filter(lambda x: x and x != 'None specified', [e["strengths"] for e in evals])) or 'None specified'
            all_concerns = " | ".join(filter(lambda x: x and x != 'None specified', [e["concerns"] for e in evals])) or 'None specified'
            all_comments = " | ".join(filter(lambda x: x and x != 'No comments', [e["comments"] for e in evals])) or 'No comments'
            all_recs = " | ".join(filter(lambda x: x and x != 'N/A', [e["recommended_product"] for e in evals])) or 'N/A'
            
            c_name_key = candidate_name.lower().strip()
            place = 'N/A'
            business_name = 'N/A'
            if c_name_key in excel_mapping:
                mapped = excel_mapping[c_name_key]
                place = mapped.get('district', 'N/A')
                business_name = mapped.get('business_name', 'N/A')
                
            candidates.append({
                "sl_no": idx + 1,
                "candidate_name": candidate_name,
                "place": place,
                "business_name": business_name,
                "scores": {
                    "interview": round(avg_interview, 2) if avg_interview != '' else '',
                    "growth": round(avg_growth, 2) if avg_growth != '' else '',
                    "need": round(avg_need, 2) if avg_need != '' else '',
                    "emotional": round(avg_emotional, 2) if avg_emotional != '' else '',
                    "sustainability": round(avg_sustainability, 2) if avg_sustainability != '' else '',
                    "utilization": round(avg_utilization, 2) if avg_utilization != '' else ''
                },
                "weighted_score": avg_weighted_score,
                "final_decision": final_decision,
                "details": {
                    "strengths": all_strengths,
                    "concerns": all_concerns,
                    "comments": all_comments,
                    "recommended_product": all_recs
                }
            })
            
        return {"candidates": candidates}
        
    except Exception as e:
        import traceback
        return {"error": f"Unexpected error: {str(e)}", "trace": traceback.format_exc()}


def _find_key(row_dict, possible_keys, default_val):
    for pk in possible_keys:
        for k in row_dict.keys():
            if pk in k:
                val = row_dict[k]
                return str(val) if val else default_val
    return default_val

def _parse_score(row_dict, possible_keys):
    val = _find_key(row_dict, possible_keys, '0')
    try:
        return float(val)
    except:
        return 0.0
def _get_excel_mapping():
    from django.core.cache import cache
    mapping = cache.get('she_start_excel_mapping')
    if mapping is not None:
        return mapping
        
    mapping = {}
    try:
        import pandas as pd
        import glob
        from django.conf import settings
        
        search_path = os.path.join(settings.BASE_DIR.parent, 'project_folder', '*She Start*Responses*.xlsx')
        excel_files = glob.glob(search_path)
        if excel_files:
            df = pd.read_excel(excel_files[0])
            for _, row in df.iterrows():
                app_name = str(row.get('1. Full Name', '')).strip().lower()
                if app_name:
                    mapping[app_name] = {
                        'district': str(row.get('5. District', 'N/A')),
                        'business_name': str(row.get('19. Name of the Business', 'N/A'))
                    }
        # Cache for 1 hour to avoid reading excel on every request
        cache.set('she_start_excel_mapping', mapping, 3600)
    except Exception as e:
        print(f"Error reading She Start Excel: {e}")
        
    return mapping
