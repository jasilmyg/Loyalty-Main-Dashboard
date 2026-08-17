import sys
sys.stdout.reconfigure(encoding='utf-8')

views_addition = '''

# =============================================================
# AI CUSTOMER TARGETING ENGINE
# =============================================================

class AITargetingView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, 'dashboard/ai_targeting.html', {
            'page_title': 'AI Customer Targeting Engine',
        })


class AITargetingAPIView(View):
    """Serves pre-computed AI customer scores from JSON cache."""

    def get(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'status': 'error', 'message': 'Authentication required'}, status=401)

        import json as _json
        import os

        scores_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                   'analytics', 'ai_targeting_scores.json')

        if not os.path.exists(scores_path):
            return JsonResponse({
                'status': 'error',
                'message': 'AI scores not generated yet.',
                'detail': 'Run: python analytics/customer_ml.py to generate scores.'
            }, status=200)

        try:
            with open(scores_path, encoding='utf-8') as f:
                data = _json.load(f)
            return JsonResponse(data, safe=True)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
'''

with open('dashboard/views.py', 'a', encoding='utf-8') as f:
    f.write(views_addition)
print('Added AITargetingView and AITargetingAPIView to views.py')
