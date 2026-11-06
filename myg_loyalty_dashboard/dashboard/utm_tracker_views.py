"""
Views for UTM Link Builder & Click Tracking Dashboard
"""

import base64
import csv
import json
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from .utm_tracker_service import (
    DEFAULT_CAMPAIGN,
    DEFAULT_TARGET_URL,
    create_or_update_utm_link,
    delete_utm_link,
    get_utm_dashboard_data,
    record_click_and_get_target,
    record_external_utm_hit,
)

# 1x1 transparent GIF for pixel tracking
PIXEL_GIF_BYTES = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


class UtmTrackerPageView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/utm_tracker.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["default_target_url"] = DEFAULT_TARGET_URL
        ctx["default_campaign"] = DEFAULT_CAMPAIGN
        return ctx


class UtmTrackerAPIView(LoginRequiredMixin, View):
    """
    GET    /api/v1/utm-tracker/ -> dashboard KPIs, links, charts, click logs
    POST   /api/v1/utm-tracker/ -> create/update link OR simulate click
    DELETE /api/v1/utm-tracker/ -> delete a link by slug
    """

    def get(self, request):
        start_date = request.GET.get("start_date", "").strip()
        end_date = request.GET.get("end_date", "").strip()
        source = request.GET.get("source", "").strip()
        campaign = request.GET.get("campaign", "").strip()

        data = get_utm_dashboard_data(
            request,
            start_date=start_date,
            end_date=end_date,
            source_filter=source,
            campaign_filter=campaign,
        )
        return JsonResponse(data)

    def post(self, request):
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            body = request.POST.dict()

        action = body.get("action", "save_link")
        if action == "simulate_click":
            slug = (body.get("slug") or "mgl-whatsapp").strip()
            target_url = record_click_and_get_target(slug, request)
            return JsonResponse({"status": "ok", "slug": slug, "redirect_to": target_url})

        link = create_or_update_utm_link(body)
        return JsonResponse({"status": "ok", "link": link})

    def delete(self, request):
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            body = {}
        slug = (body.get("slug") or request.GET.get("slug") or "").strip()
        if not slug:
            return JsonResponse({"status": "error", "message": "Missing slug"}, status=400)
        deleted = delete_utm_link(slug)
        return JsonResponse({"status": "ok", "deleted": deleted, "slug": slug})


class UtmRedirectView(View):
    """
    Public endpoint: /go/<slug>/
    Logs the click in `utm_clicks` and immediately issues a 302 redirect to
    the full UTM-tagged URL (e.g. https://mygoffers.in/karnataka/dakshina-kannada/myg-future-mangaluru?utm_source=...).
    """

    def get(self, request, slug: str):
        target_url = record_click_and_get_target(slug, request, extra_params=request.GET.dict())
        response = HttpResponseRedirect(target_url)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        return response


@method_decorator(csrf_exempt, name="dispatch")
class UtmCollectBeaconView(View):
    """
    Public CORS-enabled beacon & 1x1 pixel endpoint:
      - POST/GET /api/v1/utm-tracker/collect/
      - GET      /api/v1/utm-tracker/pixel.gif
    Allows external landing pages (like mygoffers.in via GTM) to report UTM pageviews back.
    """

    def _add_cors(self, response):
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    def options(self, request, *args, **kwargs):
        return self._add_cors(HttpResponse(status=204))

    def get(self, request, *args, **kwargs):
        record_external_utm_hit(request)
        if request.path.endswith(".gif"):
            resp = HttpResponse(PIXEL_GIF_BYTES, content_type="image/gif")
        else:
            resp = JsonResponse({"status": "ok"})
        return self._add_cors(resp)

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        res = record_external_utm_hit(request, payload=payload)
        return self._add_cors(JsonResponse(res))


class UtmClicksExportCSVView(LoginRequiredMixin, View):
    """Exports click activity or UTM link list as CSV."""

    def get(self, request):
        export_type = request.GET.get("type", "clicks")
        data = get_utm_dashboard_data(
            request,
            start_date=request.GET.get("start_date", "").strip(),
            end_date=request.GET.get("end_date", "").strip(),
            source_filter=request.GET.get("source", "").strip(),
            campaign_filter=request.GET.get("campaign", "").strip(),
        )

        response = HttpResponse(content_type="text/csv")
        if export_type == "links":
            response["Content-Disposition"] = 'attachment; filename="myG_Future_Mangaluru_UTM_Links.csv"'
            writer = csv.writer(response)
            writer.writerow([
                "Slug",
                "Title",
                "Channel",
                "utm_source",
                "utm_medium",
                "utm_campaign",
                "utm_term",
                "utm_content",
                "Full UTM Destination URL",
                "Tracked Redirect URL",
                "Total Clicks",
                "Unique Visitors",
                "Today Clicks",
                "Last Clicked At",
            ])
            for l in data["links"]:
                writer.writerow([
                    l["slug"],
                    l["title"],
                    l["channel"],
                    l["utm_source"],
                    l["utm_medium"],
                    l["utm_campaign"],
                    l["utm_term"],
                    l["utm_content"],
                    l["full_utm_url"],
                    l["short_redirect_url"],
                    l["total_clicks"],
                    l["unique_visitors"],
                    l["today_clicks"],
                    l["last_clicked_at"],
                ])
        else:
            response["Content-Disposition"] = 'attachment; filename="myG_Future_Mangaluru_UTM_Clicks_Log.csv"'
            writer = csv.writer(response)
            writer.writerow([
                "Click ID",
                "Timestamp (IST)",
                "Link Slug",
                "utm_source",
                "utm_medium",
                "utm_campaign",
                "utm_term",
                "utm_content",
                "Device",
                "OS",
                "Browser / App",
                "Referrer",
                "Masked IP",
                "Event Type",
            ])
            for c in data["recent_clicks"]:
                writer.writerow([
                    c["id"],
                    c["clicked_at"],
                    c["slug"],
                    c["utm_source"],
                    c["utm_medium"],
                    c["utm_campaign"],
                    c["utm_term"],
                    c["utm_content"],
                    c["device_type"],
                    c["os_name"],
                    c["browser_name"],
                    c["referrer"],
                    c["ip_masked"],
                    c["event_type"],
                ])
        return response

