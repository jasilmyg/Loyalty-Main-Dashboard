import sys
sys.stdout.reconfigure(encoding='utf-8')

views_addition = '''

# =============================================================
# MY PARF PERFUME — DATA DOWNLOAD
# =============================================================

class MyParfDownloadView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, 'dashboard/my_parf.html', {
            'page_title': 'MY PARF Perfume Customer Data',
        })


class MyParfDataAPIView(View):
    """Serve MY PARF CSV downloads."""

    def get(self, request, data_type):
        if not request.user.is_authenticated:
            return JsonResponse({'status': 'error', 'message': 'Authentication required'}, status=401)

        import os, csv
        from django.http import HttpResponse, Http404

        base = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'analytics')

        if data_type == 'full':
            csv_path = os.path.join(base, 'my_parf_customers.csv')
            filename = 'MY_PARF_Full_Transactions.csv'
        elif data_type == 'summary':
            csv_path = os.path.join(base, 'my_parf_customers_summary.csv')
            filename = 'MY_PARF_Customer_Summary.csv'
        elif data_type == 'mobiles':
            # Generate mobile-only file on the fly from summary
            summary_path = os.path.join(base, 'my_parf_customers_summary.csv')
            if not os.path.exists(summary_path):
                return JsonResponse({'status': 'error', 'message': 'Data not generated yet'}, status=200)
            response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
            response['Content-Disposition'] = 'attachment; filename="MY_PARF_Mobile_Numbers.csv"'
            with open(summary_path, encoding='utf-8') as f:
                reader = csv.reader(f)
                headers = next(reader)
                mobile_idx = headers.index('customer_mobile')
                writer = csv.writer(response)
                writer.writerow(['customer_mobile'])
                for row in reader:
                    if row:
                        writer.writerow([row[mobile_idx]])
            return response
        else:
            raise Http404

        if not os.path.exists(csv_path):
            return JsonResponse({
                'status': 'error',
                'message': 'Data file not found. Run extract_my_parf.py to generate.'
            }, status=200)

        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        with open(csv_path, encoding='utf-8') as f:
            response.write(f.read())
        return response
'''

with open('dashboard/views.py', 'a', encoding='utf-8') as f:
    f.write(views_addition)
print('Added MyParfDownloadView and MyParfDataAPIView')
