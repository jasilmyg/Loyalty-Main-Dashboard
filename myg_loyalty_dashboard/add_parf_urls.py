import sys
sys.stdout.reconfigure(encoding='utf-8')

with open('dashboard/urls.py', encoding='utf-8') as f:
    content = f.read()

if 'my_parf_download' not in content:
    addition = """
    # MY PARF Perfume Data Download
    path('my-parf/', views.MyParfDownloadView.as_view(), name='my_parf_download'),
    path('download/my-parf/<str:data_type>/', views.MyParfDataAPIView.as_view(), name='my_parf_data_api'),
"""
    content = content.rstrip()
    if content.endswith(']'):
        content = content[:-1] + addition + ']\n'
    with open('dashboard/urls.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Added MY PARF URL routes')
else:
    print('Already exists')
