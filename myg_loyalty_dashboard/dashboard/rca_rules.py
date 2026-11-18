"""Rating-aware, query-time classification of Google reviews into the legacy RCA cards.

Why: the stored ``rca_category`` / ``rca_subcategory`` columns come from a pure keyword matcher
(ch_load_google_reviews.categorize_rca) that ignores the rating, so 92% of the reviews in the
three "issue" cards were actually 4-5 star praise ("Good service" -> After-Sales complaint).

Rules (applied in ClickHouse, nothing is rewritten in the table):
  * Reviews are first de-duplicated (REVIEWS_SRC): the same store + same comment is counted once
    (short comments additionally need the same reviewer, so two people writing "Good" both count),
    and a long comment posted by the same reviewer to several stores is counted once.
  * Issue review   = rating <= 2, or rating = 3 AND the text clearly complains.
  * Everything else (4-5 star, or neutral 3-star without a complaint) -> "Positive & General Feedback".
  * Issue reviews go to the first matching sub-category, in the order of ISSUE_RULES.
    The order is "most specific first": call / contact failures, explicit staff behaviour, then
    concrete topics (delivery, warranty, repair, charges ...), then weaker staff cues, offers,
    store facilities, and finally the generic buckets.
  * A complaint that matches nothing is filed under Sales, Store & General Complaints >
    "Unspecified Complaint" -- it is NOT counted as an After-Sales problem any more.

``CLS_SQL`` evaluates to ``'<card>/<item>'`` for every row.
"""

# ---------------------------------------------------------------- de-duplicated review source
# row_number() works on both ClickHouse and StarRocks (the service layer falls back between them).
# Blank comments (rating-only reviews) are always kept.
REVIEWS_SRC = (
    "(SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY reviewer_name, comment ORDER BY updated_at) AS _rn2 FROM "
    "(SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY store_name, comment, "
    "if(length(comment) >= 40, '', reviewer_name) ORDER BY updated_at) AS _rn FROM google_reviews) "
    "WHERE comment = '' OR _rn = 1)) "
    "WHERE comment = '' OR length(comment) < 40 OR _rn2 = 1)"
)

# ---------------------------------------------------------------- regex fragments (RE2, lower-case text)
_LATE = r"(^|[^a-z])late([^a-z]|$)"
_HANG = r"(^|[^a-z])hang"
_RING = r"(^|[^a-z])(ring|line)"
_WORD_BAD = (r"(^|[^a-z])(bad|poor|worst|pathetic|terrible|horrible|waste|useless|crap|rubbish|disappoint[a-z]*|"
             r"unhappy|unsatisfied|dissatisf[a-z]*)([^a-z]|$)")

RUDE = (r"rude|arrogant|disrespect|misbehav|insult|shout|impolite|harsh|humiliat|abus|bad behavi|worst behavi|"
        r"behavi.{0,10}(bad|poor|worst|pathetic|horrible|terrible)|not (polite|respect)|no respect|least respect|"
        r"ill.?treat|treated.{0,20}(badly|rudely|poorly)|bad (attitude|manner)")
_RUDE_WORDS = r"rude|arrogant|shout|impolite|disrespect|harsh|misbehav|insult"
_CALL_WORDS = r"call|telephon|customer care|helpline|on (the )?phone|over (the )?phone|by phone"
CALL_CTX = r"call|phone|number|contact|helpline|customer care|reach|" + _RING

# Delivery / installation / stock: a delivery-type word close to a "late / not done / missing" cue
_DLV = r"deliver|install|dispatch|shipment|courier|(^|[^a-z])stock"
_DLV_CUE = (r"delay|" + _LATE + r"|took|days|weeks|wait|pending|cancel+ed|(^|[^a-z])(still|yet|never)([^a-z]|$)|"
            r"wrong|missing|unavailable")
# "not installed", "no delivery", "never delivered", "not in stock" ...
_DLV_NEG = r"(^|[^a-z])(not|no|never)[a-z ]{0,12}(deliver|install|dispatch|receiv|arriv|(^|[^a-z])stock)"
DELIVERY = (r"(" + _DLV + r").{0,40}(" + _DLV_CUE + r")|(" + _DLV_CUE + r").{0,40}(" + _DLV + r")|" + _DLV_NEG)

# Service / repair turnaround: a service-type word + a "too slow" cue
_SVC = (r"repair|servic(e|ing) (cent|request|team|support|section|engineer|department|appointment)|servicing|"
        r"technician|engineer|(^|[^a-z])ticket|(^|[^a-z])spare|after.?sales?|(^|[^a-z])fix")
_SLOW = (r"delay|" + _LATE + r"|waiting|waited|wait for|took .{0,15}(days|weeks|hours|month|long)|"
         r"still (not|waiting|pending)|not yet|yet to|slow|too long|so long|long (time|wait)|"
         r"[0-9]+ (days|weeks|months)|weeks|months|pending|not (done|completed|resolved|fixed)|"
         r"taking (so |too )?(long|time|more)|more time")
SVC_DELAY = r"(" + _SVC + r").{0,60}(" + _SLOW + r")|(" + _SLOW + r").{0,60}(" + _SVC + r")"

# Genuine service charges / spare cost (not shop offers or product price)
_MONEY = r"cost|charg|fee|rupees|rs[.]? ?[0-9]|[0-9] ?rs"
_SVC_STRICT = r"repair|servicing|service (cent|charge|cost|fee)|installation|technician"
SVC_CHARGE = (r"(^|[^a-z])spare.{0,25}(" + _MONEY + r"|price|amount)|(" + _MONEY + r"|price|amount).{0,25}(^|[^a-z])spare|"
              r"labou?r (charge|cost|fee)|(" + _SVC_STRICT + r").{0,40}(" + _MONEY + r")|(" + _MONEY + r").{0,40}(" + _SVC_STRICT + r")")

# Offers, advertised price, mis-selling, hidden charges
OFFERS = (r"offer|gift|scratch|cashback|discount|advertis|fake|cheat|fraud|scam|mislead|deceiv|bluff|loot|fool(ed|ing)? |"
          r"false (promise|commitment|advert)|hidden|insurance|(^|[^a-z])emi([^a-z]|$)|mrp|pric|expensive|overpric|"
          r"(^|[^a-z])cost(s|ly)?([^a-z]|$)|charg|extra (money|amount|charge|payment)|amount|coupon|voucher|"
          r"(^|[^a-z])(bill|invoice)([^a-z]|$)|rs[.]? ?[0-9]|[0-9] ?rs")

# Store environment, facilities and counter waiting -- negative wording only (so praise isn't swept in)
STORE = (r"(^|[^a-z])(no|not|poor|bad|lack of|limited|little|less|insufficient|inadequate|worst|pathetic)[a-z ]{0,12}"
         r"(parking|park|clean|space|seat|ventilat|cooling|air.?condition|drinking water|toilet|washroom|restroom|light)|"
         r"unclean|unhygien|dirty|dust|smell|stink|crowd|congest|cramped|noisy|noise|(^|[^a-z])queue|"
         r"(cash|billing) (counter|section|desk)|check.?out|messy|untidy|parking (is|was|problem|issue|area|space)|"
         r"(parking|cooling|air.?condition|(^|[^a-z])ac).{0,20}(bad|poor|crap|worst|not|problem|issue|pathetic|terrible)|"
         r"poor (cooling|ventilation|lighting|ambien[a-z]*|parking)|hot (inside|in)|sweat|escalator|elevator")

# 3-star reviews are only treated as issues when the text clearly complains
NEG_CUE_WEAK = r"(^|[^a-z])(not|no|never|lack|lacking|but|however|unfortunately|without)([^a-z]|$)|n.?t |" + _WORD_BAD
NEG_CUE_STRONG = (_WORD_BAD + r"|not ((a|so|very|that|too|at all|up to|really|much) )*(good|better|great|satisfied|happy|enough)|"
                  r"lacking|lack of|delay|could be better|needs? improvement")

# (card, item, [regexes that must ALL match])  -- ordered, first match wins
ISSUE_RULES = [
    # ---- contact failures (the channel itself failed)
    # rude + a call word within 30 characters of each other (either order)
    ('communication', 'rude_phone', [
        r"(" + _RUDE_WORDS + r").{0,30}(" + _CALL_WORDS + r")|(" + _CALL_WORDS + r").{0,30}(" + _RUDE_WORDS + r")"]),
    ('communication', 'unanswered', [
        r"not (pick|answer|respond|reachable)|n.?t (pick|answer|respond)|never (pick|answer|respond)|"
        r"(no ?one|nobody) (is )?(pick|answer|respond)|unreachable|busy tone|always busy|"
        r"line .{0,10}busy|number .{0,12}busy|disconnect|(not|unable to|n.?t|cant) (able to )?(contact|reach|get through)",
        CALL_CTX]),
    # ---- explicit staff behaviour
    ('staff', 'rude', [RUDE]),
    ('staff', 'unprofessional', [
        r"unprofessional|lack of (knowledge|training)|no (product )?knowledge|(not|n.?t) (very )?knowledgeable|"
        r"(don.?t|do not|does.?n.?t|did.?n.?t) know (anything|about|how|the)|knowledge gap|wrong (information|info|details|advice)|"
        r"misguid|clueless|incompetent|untrained|not trained|staff shortage"]),
    # ---- concrete topics
    ('after_sales', 'delivery', [DELIVERY]),
    ('communication', 'promises', [
        r"promis|assured|call.?back|(no ?one|nobody) (called|contacted|informed|got back)|"
        r"did.?n.?t (call|inform|contact|get back)|never (called|got back|informed|contacted)|"
        r"not (called|informed|contacted)|no (call|follow.?up call)"]),
    ('communication', 'zero_contact', [
        r"whatsapp.{0,30}(no|not|never)|(no|not|never).{0,30}whatsapp|(no|zero|lack of) (response|reply|communication|contact)|"
        r"not (respond|reply|responding|replying)|did.?n.?t (respond|reply)|never (respond|reply|replied|responded)|"
        r"unresponsive|(no ?one|nobody) (responded|replied)"]),
    ('after_sales', 'warranty', [
        r"warrant|replac|refund|guarantee|money back|(return|exchange) (the|my|it|this|that|product|item|phone|tv|laptop|mobile|machine)|"
        r"not (ready|willing) to (replace|return|exchange)"]),
    ('staff', 'neglect', [
        r"ignor|neglect|(no ?one|nobody) (is |was |were )?(attend|availab|there|assist|help|to (attend|assist|help|greet))|"
        r"not attend|did.?n.?t (attend|notice|bother)|walk.?in|no (assistance|attention)|least interested|"
        r"not interested in (helping|customers|attending)|(waited|waiting).{0,25}(assist|staff|someone|attend)|"
        r"staff.{0,25}(busy|chatting|gossip)|loose talk"]),
    ('after_sales', 'cost', [SVC_CHARGE]),
    ('after_sales', 'delays', [SVC_DELAY]),
    ('after_sales', 'unresolved', [
        r"not working|does.?n.?t work|did.?n.?t work|stopped working|defect|repair|fault|problem|damag|broke|broken|dead|"
        + _HANG + r"|overheat|issue|complaint|malfunction|duplicate|counterfeit|not genuine|poor quality|bad quality"]),
    ('after_sales', 'updates', [
        r"update|status|intimat|notif|(no|poor|lack of|pathetic|worst|bad|zero) follow.?up|(not|n.?t) follow|"
        r"follow.?up (is|was)? ?(bad|poor|pathetic|worst|nil|zero)|no (clarity|information)|(no|any) (message|msg)|"
        r"not (told|informed|updated)|in the dark"]),
    # ---- weaker staff cues (after the concrete topics so they don't swallow product / delivery complaints)
    ('staff', 'bias', [
        r"attitude|low.?budget|budget (customer|buyer|bias)|cheap (customer|people)|look(ed|ing) (at me|down)|"
        r"pressur(e|ed|ing) (me|us|the customer|customers)|pushy|forc(e|ed).{0,10}buy|unhelpful|"
        r"not (helpful|cooperat)|uncooperative|careless|reckless"]),
    # ---- sales / store
    ('sales', 'store', [STORE]),
    ('sales', 'offers', [OFFERS]),
    ('communication', 'chasing', [
        r"follow.?up|following up|(^|[^a-z])chas(e|ing)([^a-z]|$)|again and again|multiple times|many times|several times|"
        r"repeatedly|repeated|tired of|(called|visited|went|contacted).{0,20}([0-9]+|three|four|five|many|several|multiple|twice) "
        r"(times|days|visits)|[0-9]+ times|every day|daily"]),
    # ---- generic buckets (only when nothing more specific matched)
    ('staff', 'general', [
        r"staff|salesman|sales ?(man|men|person|people|girl|lady|executive|guy)|employee|executive|cashier|receptionist|"
        r"billing (section|staff|counter|lady|girl)|lady|girl"]),
    ('after_sales', 'general', [
        r"after.?(sale|service)|service (cent|team|section|department|engineer)|servicing|technician|installation team|call cent"]),
    ('sales', 'general_service', [
        r"customer.?(service|support|care)|(^|[^a-z])servic|support|helpline"]),
]

# Items that are broad catch-alls: they do not take part in the "does a 3-star clearly complain" test
_GENERIC_ITEMS = {'general_service'}

# Positive branch (4-5 star, or neutral 3 star with no complaint)
POSITIVE_RULES = [
    ('ambiance', [r"clean|ambian|ambienc|neat|organi[sz]ed|atmosphere|beautiful|spacious|environment|interior|well maintained|decor|parking"]),
    ('service', [r"staff|service|helpful|friendly|polite|support|team|sales ?(man|person|executive|girl|lady|guy)|salesman|"
                 r"executive|customer care|explain|knowledg|assist|courte|attend|behavi|response"]),
    ('product', [r"product|collection|variety|quality|range|stock|model|offer|price|discount|brand|choice|selection|genuine|original"]),
]

# key order matters: the page JS maps the rows of each card by position
CARD_ITEMS = {
    'after_sales': ['delivery', 'delays', 'warranty', 'unresolved', 'updates', 'cost', 'general'],
    'staff': ['unprofessional', 'rude', 'neglect', 'bias', 'general'],
    'communication': ['chasing', 'promises', 'rude_phone', 'unanswered', 'zero_contact'],
    'sales': ['offers', 'store', 'general_service', 'unspecified'],
    'other': ['praise', 'ambiance', 'service', 'product', 'unclassified'],
}


def _lit(s):
    """ClickHouse string literal."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _m(regexes):
    return " AND ".join(f"match(lowerUTF8(comment), {_lit(r)})" for r in regexes)


_ANY_RULE = " OR ".join(f"({_m(r)})" for _c, i, r in ISSUE_RULES if i not in _GENERIC_ITEMS)
# no usable text (empty / emoji-only / legacy Empty/No Comment)
_NO_TEXT = f"(comment = '' OR hf_category = 'Empty/No Comment' OR NOT match(comment, {_lit(r'\p{L}|\p{N}')}))"

ISSUE_COND = (
    f"(rating <= 2 OR (rating = 3 AND (match(lowerUTF8(comment), {_lit(NEG_CUE_STRONG)}) "
    f"OR (({_ANY_RULE}) AND match(lowerUTF8(comment), {_lit(NEG_CUE_WEAK)})))))"
)

_issue_branches = ", ".join(f"{_m(r)}, {_lit(c + '/' + i)}" for c, i, r in ISSUE_RULES)
_positive_branches = ", ".join(f"{_m(r)}, {_lit('other/' + i)}" for i, r in POSITIVE_RULES)

CLS_SQL = (
    f"multiIf({ISSUE_COND}, multiIf({_issue_branches}, 'sales/unspecified'), "
    f"{_NO_TEXT}, 'other/unclassified', "
    f"{_positive_branches}, 'other/praise')"
)
