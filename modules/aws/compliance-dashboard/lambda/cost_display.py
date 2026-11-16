"""
Cost data interpretation and display — single source of truth.

The compliance scanner produces two types of cost data:

1. **Per-resource estimated_monthly_cost** (float | None)
   Source: Scanner's ``_enrich_costs()`` in ``aws_resources.py``
   - float > 0  — Forward-looking monthly rate from instance-type pricing tables
   - float == 0 — Genuinely free (config-only: IAM, SGs, ACM, task defs, etc.)
   - None       — Scanner has NO pricing model for this resource type.
                   Typically usage-based (CloudTrail, GuardDuty, data transfer,
                   API calls, log ingestion).  The cost is real but unknowable
                   from resource metadata alone.

2. **Per-AWS-service service_costs** (dict[str, float])
   Source: Cost Explorer ``GetCostAndUsage`` API (trailing 30 days)
   - Backward-looking *actual* charges
   - Keyed by AWS service name (e.g. "Amazon Elastic Compute Cloud - Compute")
   - NOT per-resource — shared across all resources of that AWS service type

Display rules:
  - NEVER show $0 when the real answer is "unknown" (None)
  - Label estimated costs with "Est." to distinguish from actual billing
  - When estimated is None, show "no model" with a tooltip explanation
  - When billed data is not available per-resource, say so
  - Link to /burn for detailed cost analysis

This module is imported by all views that display cost data.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Numeric formatting (no HTML — safe for any context)
# ─────────────────────────────────────────────────────────────────────────────


def fmt_cost(val):
    """Format a *known* numeric cost for display.

    Callers MUST check for None before calling.  For HTML output that
    auto-handles None, use ``fmt_cost_est()`` or ``fmt_cost_billed()``.
    """
    if val is None:
        raise ValueError("fmt_cost() received None — use fmt_cost_est() for HTML output")
    if val < 0.01:
        return "~$0"
    if val < 1:
        return f"${val:.2f}"
    if val < 10:
        return f"${val:.1f}"
    return f"${val:,.0f}"


def cost_tier_color(val):
    """Return a CSS hex color for a dollar cost value.

    Returns the "unknown" gray for None.
    """
    if val is None:
        return "#4b5563"
    if val <= 1:
        return "#22c55e"
    if val <= 15:
        return "#3b82f6"
    if val <= 50:
        return "#f59e0b"
    return "#ef4444"


# ─────────────────────────────────────────────────────────────────────────────
# HTML cell formatters — return ready-to-embed HTML strings.
#
# These use the ``.hint`` CSS class which requires HINT_CSS on the page and
# HINT_JS for tooltip behavior.
# ─────────────────────────────────────────────────────────────────────────────


def fmt_cost_est(val):
    """Format an estimated cost for HTML display.

    Returns plain text for known values, a ``.hint`` span for None.
    """
    if val is None:
        return (
            '<span class="hint" style="color:#4b5563;font-size:10px;" '
            'data-tip="Scanner has no pricing model for this resource type. '
            "Cost depends on usage volume (API calls, events, data transfer), "
            "not resource count. Check the burn page for actual charges."
            '">no model</span>'
        )
    return fmt_cost(val)


def fmt_cost_billed(val):
    """Format a billed (actual) cost for HTML display.

    Returns plain text for known values, a ``.hint`` span for None.
    """
    if val is None:
        return (
            '<span class="hint" style="color:#4b5563;font-size:10px;" '
            'data-tip="Per-resource billing is not available. AWS bills by '
            "service, not by individual resource. See the burn page for "
            'per-service actuals.">per svc</span>'
        )
    if val == 0:
        return (
            '<span class="hint" style="color:#22c55e;font-size:10px;" '
            'data-tip="This service has not appeared on the AWS bill. '
            "Usage is within the free tier, rounds to $0.00, or "
            "the service hasn't been used this billing period."
            '">$0</span>'
        )
    return fmt_cost(val)


def fmt_cost_badge(val, cost_unknown=0):
    """Format a module-level cost as a badge label for service cards.

    Handles three cases:
      - val > 0, no unknowns   → "$X/mo"
      - val >= 0, some unknown  → "$X/mo*" (asterisk = partial)
      - val == 0, all unknown   → "no model"

    Returns ``(label, color, tooltip)`` tuple.
    """
    if val is None:
        val = 0
    if val == 0 and cost_unknown > 0:
        return (
            "no model",
            "#4b5563",
            "Scanner has no pricing model for any resource in this module",
        )
    color = cost_tier_color(val)
    label = fmt_cost(val) + "/mo"
    if cost_unknown > 0:
        label += "*"
        tip = f"{cost_unknown} resource(s) have no pricing model — actual cost may be higher"
        return label, color, tip
    return label, color, ""


# ─────────────────────────────────────────────────────────────────────────────
# Summary helpers — for stat pills and module-level rollups
# ─────────────────────────────────────────────────────────────────────────────


def sum_costs(resources, key="estimated_monthly_cost"):
    """Sum costs from a list of resource dicts, skipping None values.

    Returns ``(total, unknown_count)`` where total is the sum of known
    costs and unknown_count is how many had None.
    """
    total = 0.0
    unknown = 0
    for r in resources:
        val = r.get(key)
        if val is None:
            unknown += 1
        else:
            total += float(val)
    return total, unknown


# ─────────────────────────────────────────────────────────────────────────────
# Shared CSS + JS for hint tooltips.
#
# Include HINT_CSS in <style> and HINT_JS in <script> on any page that uses
# fmt_cost_est() / fmt_cost_billed() / fmt_cost_badge() output.
# ─────────────────────────────────────────────────────────────────────────────

HINT_CSS = """
  .hint {
    cursor: help;
    border-bottom: 1px dotted currentColor;
  }
  .hint-tip {
    position: fixed; padding: 6px 10px;
    background: #1e1e2e; color: #e2e8f0; border: 1px solid #2a2a3a;
    border-radius: 6px; font-size: 10px; line-height: 1.4;
    max-width: 280px; z-index: 1000;
    pointer-events: none; opacity: 0; transition: opacity 0.15s;
  }
  .hint-tip.visible { opacity: 1; }
"""

# Double-braced for safe embedding inside f-strings.
HINT_CSS_FSTR = HINT_CSS.replace("{", "{{").replace("}", "}}")

HINT_JS = """<script>(function(){
  var tip=document.createElement('div');
  tip.className='hint-tip';
  document.body.appendChild(tip);
  document.addEventListener('mouseenter',function(e){
    var h=(e.target instanceof Element?e.target:document.documentElement).closest('.hint');
    if(!h)return;
    tip.textContent=h.getAttribute('data-tip');
    tip.classList.add('visible');
    var r=h.getBoundingClientRect();
    var above=r.top-tip.offsetHeight-4;
    tip.style.top=(above>0?above:(r.bottom+4))+'px';
    var left=r.left;
    if(left+280>window.innerWidth) left=window.innerWidth-290;
    if(left<4) left=4;
    tip.style.left=left+'px';
  },true);
  document.addEventListener('mouseleave',function(e){
    if((e.target instanceof Element?e.target:document.documentElement).closest('.hint')) tip.classList.remove('visible');
  },true);
})()</script>"""

# Double-braced for safe embedding inside f-strings.
HINT_JS_FSTR = HINT_JS.replace("{", "{{").replace("}", "}}")
