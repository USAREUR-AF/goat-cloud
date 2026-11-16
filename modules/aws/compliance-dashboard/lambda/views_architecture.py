"""
Architecture Diagram — landing page.

Links to the architecture visualization views:

- arch_custom.py            — Custom HTML/CSS containment diagram (zero deps)
- views_interactions.py     — D3 force-directed interaction graph
"""

import urllib.parse

from arch_shared import get_module_meta
from views_base import ENVIRONMENT, NAV_CSS, _e, render_nav


def render_architecture(user_email=""):
    """Render the architecture comparison landing page."""
    ENVIRONMENT.upper()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Architecture Views // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    background: #050508; color: #e2e8f0; line-height: 1.5; min-height: 100vh;
  }}
  ::selection {{ background: rgba(59,130,246,0.3); }}

  {NAV_CSS}

  .container {{ max-width: 900px; margin: 0 auto; padding: 32px 24px 64px; }}

  h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 8px; }}
  .subtitle {{ color: #6b7280; font-size: 13px; margin-bottom: 32px; }}

  .card-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 700px) {{ .card-grid {{ grid-template-columns: 1fr; }} }}

  .card {{
    background: #0a0a10; border: 1px solid #1e1e2e; border-radius: 10px;
    padding: 24px; transition: all 0.2s; display: flex; flex-direction: column;
  }}
  .card:hover {{
    border-color: #2a2a3a; background: #0e0e16;
    transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.3);
  }}
  .card-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }}
  .card-letter {{
    width: 32px; height: 32px; border-radius: 8px; display: flex;
    align-items: center; justify-content: center; font-weight: 700;
    font-size: 16px; flex-shrink: 0;
  }}
  .card-title {{ font-size: 15px; font-weight: 600; }}
  .card-tech {{ font-size: 10px; color: #4b5563; margin-top: 2px; }}
  .card-desc {{
    font-size: 12px; color: #94a3b8; line-height: 1.6; flex: 1;
    margin-bottom: 16px;
  }}
  .card-tags {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }}
  .card-tag {{
    font-size: 9px; padding: 2px 8px; border-radius: 3px;
    border: 1px solid rgba(255,255,255,0.06); color: #6b7280;
  }}
  .card-tag.green {{ color: #22c55e; border-color: rgba(34,197,94,0.2); background: rgba(34,197,94,0.06); }}
  .card-tag.amber {{ color: #f59e0b; border-color: rgba(245,158,11,0.2); background: rgba(245,158,11,0.06); }}
  .card-tag.red {{ color: #ef4444; border-color: rgba(239,68,68,0.2); background: rgba(239,68,68,0.06); }}
  .card-link {{
    display: inline-flex; align-items: center; gap: 6px;
    color: #3b82f6; text-decoration: none; font-size: 13px; font-weight: 600;
    padding: 8px 16px; border: 1px solid rgba(59,130,246,0.2); border-radius: 6px;
    background: rgba(59,130,246,0.06); transition: all 0.15s; align-self: flex-start;
  }}
  .card-link:hover {{
    background: rgba(59,130,246,0.12); border-color: rgba(59,130,246,0.4);
  }}

  .footer {{
    margin-top: 32px; text-align: center; font-size: 10px; color: #374151;
    padding-bottom: 16px;
  }}
  .footer a {{ color: #3b82f6; text-decoration: none; }}
  .footer a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
{render_nav("architecture", user_email)}

<div class="container">
  <h1>Architecture Visualizations</h1>
  <div class="subtitle">
    Auto-generated architecture visualization from live Terraform state.
  </div>

  <div class="card-grid">
    <!-- Approach A: Custom HTML/CSS -->
    <div class="card">
      <div class="card-header">
        <div class="card-letter" style="background:rgba(34,197,94,0.12); color:#22c55e;">A</div>
        <div>
          <div class="card-title">Custom HTML/CSS</div>
          <div class="card-tech">Pure Python &rarr; inline HTML + SVG icons</div>
        </div>
      </div>
      <div class="card-desc">
        Static containment diagram &mdash; services grouped by network zone
        (VPC, subnets, serverless, security). Shows compliance status, auth badges,
        and cost overlays at a glance. Click any service for sub-module breakdown and cost heat map.
      </div>
      <div class="card-tags">
        <span class="card-tag green">Zero deps</span>
        <span class="card-tag green">Cost overlay</span>
        <span class="card-tag">Zone grouping</span>
        <span class="card-tag">Service drill-down</span>
      </div>
      <a href="/architecture/custom" class="card-link">View Custom Diagram &rarr;</a>
    </div>

    <!-- Service Index -->
    <div class="card">
      <div class="card-header">
        <div class="card-letter" style="background:rgba(168,85,247,0.12); color:#a855f7;">B</div>
        <div>
          <div class="card-title">Service Index</div>
          <div class="card-tech">Per-service detail &rarr; architecture + cost</div>
        </div>
      </div>
      <div class="card-desc">
        Per-service deep dive &mdash; sub-module structure, resource counts,
        cost attribution. The interactive D3 graph shows service-to-service
        connections with typed edges (HTTP, auth, data, event) and expandable
        infrastructure components.
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        {
        "".join(
            f'<a href="/architecture/custom/module.{_e(urllib.parse.quote(addr.replace("module.", ""), safe=""))}" '
            f'class="card-link" style="font-size:11px;padding:5px 12px;">'
            f"{_e(meta.get('name', addr))}</a>"
            for addr, meta in sorted(get_module_meta().items(), key=lambda x: x[1].get("name", x[0]))
            if meta.get("tier", "hosted") == "hosted"
        )
    }
      </div>
      <a href="/architecture/interactions" class="card-link" style="margin-top:12px;">
        All Services &mdash; Interactive D3 Graph &rarr;</a>
    </div>

  </div>

  <div class="footer">
    All diagrams auto-generated from live Terraform state &middot;
    Only deployed modules are shown<br>
    <a href="/tree">Tree</a> &mdash; hierarchical module/resource breakdown &middot;
    <a href="/network">Network</a> &mdash; full resource inventory + compliance scoring &middot;
    <a href="/">Dashboard</a>
  </div>
</div>
</body>
</html>"""
