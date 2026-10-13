"""Rating-aware, query-time classification of Google reviews into the legacy RCA cards.

Why: the stored ``rca_category`` / ``rca_subcategory`` columns come from a pure keyword matcher
(ch_load_google_reviews.categorize_rca) that ignores the rating, so 92% of the reviews in the
three "issue" cards were actually 4-5 star praise ("Good service" -> After-Sales complaint).

Rules (applied in ClickHouse, nothing is rewritten in the table):
  * Issue review   = rating <= 2, or rating = 3 AND the text clearly complains.
  * Everything else (4-5 star, or neutral 3-star without a complaint) -> "Positive & General Feedback".
  * Issue reviews go to the first matching sub-category, in the order of ISSUE_RULES.
    Anything left over is filed under After-Sales > General Service Dissatisfaction.

``CLS_SQL`` evaluates to ``'<card>/<item>'`` for every row.
"""

# ---------------------------------------------------------------- regex fragments (RE2, lower-case text)
_LATE = r"(^|[^a-z])late([^a-z]|$)"
_COST = r"(^|[^a-z])cost(s|ly)?([^a-z]|$)"
_EMI = r"(^|[^a-z])emi([^a-z]|$)"
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

# 3-star reviews are only treated as issues when the text clearly complains
NEG_CUE_WEAK = r"(^|[^a-z])(not|no|never|lack|lacking|but|however|unfortunately|without)([^a-z]|$)|n.?t |" + _WORD_BAD
NEG_CUE_STRONG = (_WORD_BAD + r"|not ((a|so|very|that|too|at all|up to|really|much) )*(good|better|great|satisfied|happy|enough)|"
                  r"lacking|lack of|delay|could be better|needs? improvement")

# (card, item, [regexes that must ALL match])  -- ordered, first match wins
ISSUE_RULES = [
    # rude + a call word within 60 characters of each other (either order)
    ('communication', 'rude_phone', [
        r"(" + _RUDE_WORDS + r").{0,30}(" + _CALL_WORDS + r")|(" + _CALL_WORDS + r").{0,30}(" + _RUDE_WORDS + r")"]),
    ('communication', 'unanswered', [
        r"not (pick|answer|respond|reachable)|n.?t (pick|answer|respond)|never (pick|answer|respond)|"
        r"(no ?one|nobody) (is )?(pick|answer|respond)|unreachable|busy tone|always busy|"
        r"line .{0,10}busy|number .{0,12}busy|disconnect|(not|unable to|n.?t|cant) (able to )?(contact|reach|get through)",
        CALL_CTX]),
    ('communication', 'promises', [
        r"promis|assured|call.?back|(no ?one|nobody) (called|contacted|informed|got back)|"
        r"did.?n.?t (call|inform|contact|get back)|never (called|got back|informed|contacted)|"
        r"not (called|informed|contacted)|no (call|follow.?up call)"]),
    ('communication', 'zero_contact', [
        r"whatsapp.{0,30}(no|not|never)|(no|not|never).{0,30}whatsapp|(no|zero|lack of) (response|reply|communication|contact)|"
        r"not (respond|reply|responding|replying)|did.?n.?t (respond|reply)|never (respond|reply|replied|responded)|"
        r"unresponsive|(no ?one|nobody) (responded|replied)"]),
    ('staff', 'rude', [RUDE]),
    ('staff', 'neglect', [
        r"ignor|neglect|(no ?one|nobody) (attended|was (available|there)|to (attend|assist|help|greet))|"
        r"not attend|did.?n.?t (attend|notice|bother)|walk.?in|no (assistance|attention)|least interested|"
        r"not interested in (helping|customers|attending)|(waited|waiting).{0,25}(assist|staff|someone|attend)|"
        r"staff.{0,25}(busy|chatting|gossip)|loose talk"]),
    ('staff', 'unprofessional', [
        r"unprofessional|lack of (knowledge|training)|no (product )?knowledge|(not|n.?t) (very )?knowledgeable|"
        r"(don.?t|do not|does.?n.?t|did.?n.?t) know (anything|about|how|the)|knowledge gap|wrong (information|info|details|advice)|"
        r"misguid|clueless|incompetent|untrained|not trained|staff shortage"]),
    ('staff', 'bias', [
        r"attitude|low.?budget|budget (customer|buyer|bias)|cheap (customer|people)|look(ed|ing) (at me|down)|"
        r"pressur(e|ed|ing) (me|us|the customer|customers)|pushy|forc(e|ed).{0,10}buy|unhelpful|"
        r"not (helpful|cooperat)|uncooperative|careless|reckless"]),
    ('after_sales', 'warranty', [
        r"warrant|replac|refund|exchange|guarantee|not (ready|willing) to (replace|return|exchange)|money back"]),
    ('after_sales', 'delays', [
        r"delay|" + _LATE + r"|took .{0,15}(days|weeks|hours|month|long)|waiting|waited|wait for|long (time|wait|queue)|"
        r"still (not|waiting|pending)|not yet|yet to|no delivery|not delivered|undelivered|slow|too long|so long|"
        r"[0-9]+ (days|weeks|hours|months|hrs)|hours|queue|pending|not (done|completed|received)"]),
    ('after_sales', 'unresolved', [
        r"not working|does.?n.?t work|did.?n.?t work|stopped working|defect|repair|fault|problem|damag|broke|broken|dead|"
        + _HANG + r"|overheat|issue|complaint|malfunction|duplicate|counterfeit|not genuine|poor quality|bad quality"]),
    ('after_sales', 'updates', [
        r"update|status|intimat|notif|(no|poor|lack of|pathetic|worst|bad|zero) follow.?up|(not|n.?t) follow|"
        r"follow.?up (is|was)? ?(bad|poor|pathetic|worst|nil|zero)|no (clarity|information)|(no|any) (message|msg)|"
        r"not (told|informed|updated)|in the dark"]),
    ('after_sales', 'cost', [
        r"price|" + _COST + r"|charg|expensive|overpric|extra (money|amount|charge|payment|cost)|spare|offer|fake|cheat|"
        r"fraud|scam|mrp|hidden|(^|[^a-z])(bill|invoice)([^a-z]|$)|" + _EMI + r"|amount|discount|cashback|gift|deceiv|mislead|"
        r"false (promise|commitment|advert)|advertis|rs[.]? ?[0-9]|[0-9] ?rs"]),
    ('communication', 'chasing', [
        r"follow.?up|following up|(^|[^a-z])chas(e|ing)([^a-z]|$)|again and again|multiple times|many times|several times|"
        r"repeatedly|repeated|tired of|(called|visited|went|contacted).{0,20}([0-9]+|three|four|five|many|several|multiple|twice) "
        r"(times|days|visits)|[0-9]+ times|every day|daily"]),
    ('staff', 'general', [
        r"staff|salesman|sales ?(man|men|person|people|girl|lady|executive|guy)|employee|executive|cashier|receptionist|"
        r"billing (section|staff|counter|lady|girl)|lady|girl"]),
]

# Positive branch (4-5 star, or neutral 3 star with no complaint)
POSITIVE_RULES = [
    ('ambiance', [r"clean|ambian|ambienc|neat|organi[sz]ed|atmosphere|beautiful|spacious|environment|interior|well maintained|decor|parking"]),
    ('service', [r"staff|service|helpful|friendly|polite|support|team|sales ?(man|person|executive|girl|lady|guy)|salesman|"
                 r"executive|customer care|explain|knowledg|assist|courte|attend|behavi|response"]),
    ('product', [r"product|collection|variety|quality|range|stock|model|offer|price|discount|brand|choice|selection|genuine|original"]),
]

CARD_ITEMS = {
    'after_sales': ['delays', 'warranty', 'unresolved', 'updates', 'cost', 'general'],
    'staff': ['unprofessional', 'rude', 'neglect', 'bias', 'general'],
    'communication': ['chasing', 'promises', 'rude_phone', 'unanswered', 'zero_contact'],
    'other': ['praise', 'ambiance', 'service', 'product', 'unclassified'],
}


def _lit(s):
    """ClickHouse string literal."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _m(regexes):
    return " AND ".join(f"match(lowerUTF8(comment), {_lit(r)})" for r in regexes)


_ANY_RULE = " OR ".join(f"({_m(r)})" for _c, _i, r in ISSUE_RULES)
# no usable text (emoji / blank) -- same definition the legacy 'Misc. / Unclassified' item used
_NO_TEXT = ("(comment = '' OR (rca_category = 'Other / Unclassified' AND rca_subcategory = 'Unclassified' "
            "AND hf_category IN ('Empty/No Comment', '')))")

ISSUE_COND = (
    f"(rating <= 2 OR (rating = 3 AND (match(lowerUTF8(comment), {_lit(NEG_CUE_STRONG)}) "
    f"OR (({_ANY_RULE}) AND match(lowerUTF8(comment), {_lit(NEG_CUE_WEAK)})))))"
)

_issue_branches = ", ".join(f"{_m(r)}, {_lit(c + '/' + i)}" for c, i, r in ISSUE_RULES)
_positive_branches = ", ".join(f"{_m(r)}, {_lit('other/' + i)}" for i, r in POSITIVE_RULES)

CLS_SQL = (
    f"multiIf({ISSUE_COND}, multiIf({_issue_branches}, 'after_sales/general'), "
    f"{_NO_TEXT}, 'other/unclassified', "
    f"{_positive_branches}, 'other/praise')"
)
