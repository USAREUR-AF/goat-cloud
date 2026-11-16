"""
D3 Force-Directed Network Visualization of Full Account Infrastructure.

Full-screen interactive page — D3 from CDN, graph data embedded as JSON.
Shows ALL discovered resources: TF-managed (grouped by module) and
unmanaged (grouped by service/tag). Click-to-expand, rich tooltips,
AWS Console links, tag intelligence.
"""

from views_base import NAV_CSS_FIXED, _e, _js_json, render_nav


def render_network(graph, user_email=""):
    """Render a full-page D3 force-directed network visualization."""
    data_json = _js_json(graph, separators=(",", ":"))
    stats = graph.get("stats", {})

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Infra Network // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #050508; color: #e2e8f0; font-family: 'SFMono-Regular', Consolas, monospace;
         overflow: hidden; }}
  {NAV_CSS_FIXED}
  .controls .stats {{ display: flex; gap: 16px; font-size: 11px; }}
  .controls .stat {{ display: flex; align-items: center; gap: 4px; color: #6b7280; }}
  .controls .stat .dot {{ width: 8px; height: 8px; border-radius: 50%; }}
  canvas#bg {{ position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 0; }}
  svg {{ position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 1; }}
  .tooltip {{ position: fixed; pointer-events: none; background: rgba(8,8,12,0.94);
              backdrop-filter: blur(16px);
              border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 14px 18px;
              font-size: 12px; max-width: 420px; z-index: 200;
              box-shadow: 0 12px 48px rgba(0,0,0,0.6); opacity: 0; transition: opacity 0.12s; }}
  .tooltip.visible {{ opacity: 1; }}
  .tooltip .tt-title {{ font-weight: 700; font-size: 14px; margin-bottom: 3px; }}
  .tooltip .tt-type {{ color: #6b7280; font-size: 10px; text-transform: uppercase;
                       letter-spacing: 0.1em; margin-bottom: 8px; }}
  .tooltip .tt-row {{ display: flex; justify-content: space-between; gap: 16px; font-size: 11px;
                      padding: 2px 0; }}
  .tooltip .tt-label {{ color: #6b7280; }}
  .tooltip .tt-value {{ color: #e2e8f0; font-weight: 600; text-align: right; }}
  .tooltip .tt-bar {{ height: 3px; border-radius: 2px; background: rgba(255,255,255,0.06);
                      margin-top: 8px; overflow: hidden; }}
  .tooltip .tt-bar-fill {{ height: 100%; border-radius: 2px; }}
  .tooltip .tt-tags {{ margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px; }}
  .tooltip .tt-tag {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08);
                      border-radius: 3px; padding: 1px 6px; font-size: 9px; color: #94a3b8; }}
  .tooltip .tt-link {{ display: inline-block; margin-top: 8px; color: #3b82f6; font-size: 10px;
                       text-decoration: none; }}
  .legend {{ position: fixed; bottom: 24px; left: 24px; z-index: 90;
             background: rgba(8,8,12,0.85); backdrop-filter: blur(12px);
             border: 1px solid rgba(255,255,255,0.06); border-radius: 10px;
             padding: 14px 18px; font-size: 11px; }}
  .legend-title {{ font-weight: 700; margin-bottom: 6px; color: #94a3b8; font-size: 10px;
                   letter-spacing: 0.1em; }}
  .legend-row {{ display: flex; align-items: center; gap: 8px; margin: 5px 0; color: #6b7280; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  .hint {{ position: fixed; bottom: 24px; right: 24px; z-index: 90;
           color: #374151; font-size: 10px; text-align: right; line-height: 1.6; }}
  .hint kbd {{ background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06);
               border-radius: 3px; padding: 1px 5px; font-size: 9px; }}
</style>
</head>
<body>
{render_nav("network", user_email)}
<div style="position:fixed;top:72px;left:50%;transform:translateX(-50%);z-index:90;
            display:flex;gap:12px;align-items:center;background:rgba(8,8,12,0.85);
            backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,0.06);
            border-radius:8px;padding:6px 16px;font-size:11px;">
  <div id="tf-toggle" style="display:inline-flex;border:1px solid rgba(59,130,246,0.3);border-radius:4px;overflow:hidden;font-family:inherit;">
    <button id="btn-all" onclick="setFilter(false)" style="padding:4px 10px;border:none;cursor:pointer;font-family:inherit;font-size:11px;transition:all 0.15s;background:rgba(59,130,246,0.2);color:#3b82f6;font-weight:600;">All</button>
    <button id="btn-tf" onclick="setFilter(true)" style="padding:4px 10px;border:none;border-left:1px solid rgba(59,130,246,0.3);cursor:pointer;font-family:inherit;font-size:11px;transition:all 0.15s;background:transparent;color:#6b7280;">TF Only</button>
  </div>
  <span style="color:#6b7280;display:flex;align-items:center;gap:4px;"><span style="width:8px;height:8px;border-radius:50%;background:#3b82f6;box-shadow:0 0 6px #3b82f6;display:inline-block;"></span> TF ({stats.get("managed", 0)})</span>
  <span style="color:#6b7280;display:flex;align-items:center;gap:4px;"><span style="width:8px;height:8px;border-radius:50%;background:#a78bfa;box-shadow:0 0 6px #a78bfa;display:inline-block;"></span> Unmanaged ({stats.get("unmanaged", 0)})</span>
  <span style="color:#4b5563;">{stats.get("total", 0)} resources</span>
</div>
<canvas id="bg"></canvas>
<svg id="canvas"></svg>
<div class="tooltip" id="tooltip"></div>
<div class="legend">
  <div class="legend-title">LEGEND</div>
  <div class="legend-row"><span class="legend-dot" style="background:#3b82f6;box-shadow:0 0 8px rgba(59,130,246,0.5);"></span> TF Module (click to expand)</div>
  <div class="legend-row"><span class="legend-dot" style="background:#a78bfa;box-shadow:0 0 8px rgba(167,139,250,0.5);"></span> Service Group (unmanaged)</div>
  <div class="legend-row"><span class="legend-dot" style="background:#22c55e;"></span> Compliant resource</div>
  <div class="legend-row"><span class="legend-dot" style="background:#ef4444;"></span> Non-compliant resource</div>
  <div class="legend-row" style="margin-top:6px;">
    <span style="width:10px;height:10px;border-radius:50%;border:2px solid #6b7280;display:inline-block;"></span> Unmanaged (ring)
  </div>
  <div class="legend-row">
    <span style="width:10px;height:10px;border-radius:50%;background:#6b7280;display:inline-block;"></span> Managed (filled)
  </div>
</div>
<div class="hint">
  <kbd>scroll</kbd> zoom &middot; <kbd>drag</kbd> pan<br>
  <kbd>click</kbd> group: expand &middot; <kbd>dblclick</kbd> zoom in<br>
  <kbd>click</kbd> resource: open / choose destination<br>
  <kbd>N</kbd> non-compliant &middot; <kbd>A</kbd> all &middot; <kbd>T</kbd> TF only &middot; <kbd>F</kbd> fit &middot; <kbd>R</kbd> reset
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js" integrity="sha512-vc58qvvBdrDR4etbxMdlTt4GBQk1qjvyORR2nrsPsFPyrs+/u5c3+1Ct6upOgdZoIl7eq6k3a1UPDSNAQi/32A==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
const DATA = {data_json};
const W = window.innerWidth, H = window.innerHeight;

// ═══════════════════════════════════════════════════════════════
// Starfield background
// ═══════════════════════════════════════════════════════════════
const bgCanvas = document.getElementById("bg");
bgCanvas.width = W; bgCanvas.height = H;
const bgCtx = bgCanvas.getContext("2d");
const stars = Array.from({{length: 200}}, () => ({{
  x: Math.random() * W, y: Math.random() * H,
  r: Math.random() * 1.2 + 0.2,
  a: Math.random() * 0.4 + 0.05,
  speed: Math.random() * 0.0003 + 0.0001
}}));

function drawStars(t) {{
  bgCtx.clearRect(0, 0, W, H);
  stars.forEach(s => {{
    const flicker = 0.5 + 0.5 * Math.sin(t * s.speed * 1000 + s.x);
    bgCtx.beginPath();
    bgCtx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
    bgCtx.fillStyle = `rgba(148,163,184,${{s.a * flicker}})`;
    bgCtx.fill();
  }});
  requestAnimationFrame(drawStars);
}}
requestAnimationFrame(drawStars);

// ═══════════════════════════════════════════════════════════════
// SVG setup
// ═══════════════════════════════════════════════════════════════
const svg = d3.select("#canvas");
const tooltip = d3.select("#tooltip");
const g = svg.append("g");
const linkLayer = g.append("g").attr("class", "links");
const particleLayer = g.append("g").attr("class", "particles");
const ringLayer = g.append("g").attr("class", "rings");
const nodeLayer = g.append("g").attr("class", "nodes");
const labelLayer = g.append("g").attr("class", "labels");

const zoomBehavior = d3.zoom()
  .scaleExtent([0.08, 10])
  .on("zoom", (e) => g.attr("transform", e.transform));
svg.call(zoomBehavior);
svg.call(zoomBehavior.transform, d3.zoomIdentity.translate(W/2, H/2).scale(0.7));

// ═══════════════════════════════════════════════════════════════
// SVG Filters (glow)
// ═══════════════════════════════════════════════════════════════
const defs = svg.append("defs");
[["glow-green","#22c55e"],["glow-red","#ef4444"],["glow-amber","#f59e0b"],
 ["glow-blue","#3b82f6"],["glow-purple","#a78bfa"]].forEach(([id,color]) => {{
  const f = defs.append("filter").attr("id",id).attr("x","-80%").attr("y","-80%").attr("width","260%").attr("height","260%");
  f.append("feGaussianBlur").attr("stdDeviation","5").attr("result","blur");
  f.append("feFlood").attr("flood-color",color).attr("flood-opacity","0.3").attr("result","color");
  f.append("feComposite").attr("in","color").attr("in2","blur").attr("operator","in").attr("result","glow");
  const m = f.append("feMerge");
  m.append("feMergeNode").attr("in","glow");
  m.append("feMergeNode").attr("in","SourceGraphic");
}});
const shadowFilter = defs.append("filter").attr("id","text-shadow").attr("x","-20%").attr("y","-20%").attr("width","140%").attr("height","140%");
shadowFilter.append("feGaussianBlur").attr("in","SourceAlpha").attr("stdDeviation","2");
shadowFilter.append("feOffset").attr("dx","0").attr("dy","1");
shadowFilter.append("feComponentTransfer").append("feFuncA").attr("type","linear").attr("slope","0.6");
const sm = shadowFilter.append("feMerge");
sm.append("feMergeNode");
sm.append("feMergeNode").attr("in","SourceGraphic");

// ═══════════════════════════════════════════════════════════════
// Index data: build lookup maps from flat graph
// ═══════════════════════════════════════════════════════════════
const nodeMap = {{}};
DATA.nodes.forEach(n => {{ nodeMap[n.id] = n; }});

// Build module/group -> child resources mapping
const groupChildren = {{}};
DATA.links.forEach(l => {{
  if (l.link_type === "module-resource" || l.link_type === "group-resource") {{
    const parent = l.source;
    if (!groupChildren[parent]) groupChildren[parent] = [];
    groupChildren[parent].push(l.target);
  }}
}});

const commonTags = DATA.common_tags || {{}};

// ═══════════════════════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════════════════════
let expandedGroups = new Set();
let simulation;
let currentNodes = [];
let currentLinks = [];
let tfOnlyMode = false;

function nodeColor(d) {{
  if (d.type === "root") return "#3b82f6";
  if (d.type === "resource") return d.compliant ? "#22c55e" : "#ef4444";
  if (d.type === "module") {{
    if (d.pct >= 100) return "#3b82f6";
    if (d.pct >= 70) return "#f59e0b";
    return "#ef4444";
  }}
  if (d.type === "service_group") {{
    if (d.pct >= 100) return "#a78bfa";
    if (d.pct >= 70) return "#f59e0b";
    return "#ef4444";
  }}
  return "#6b7280";
}}

function glowId(d) {{
  if (d.type === "root") return "url(#glow-blue)";
  if (d.type === "resource") return d.compliant ? "url(#glow-green)" : "url(#glow-red)";
  if (d.type === "module") return "url(#glow-blue)";
  if (d.type === "service_group") return "url(#glow-purple)";
  return "";
}}

function nodeRadius(d) {{
  if (d.type === "root") return 30;
  if (d.type === "resource") return 14;
  if (d.type === "module") return Math.max(16, Math.min(36, 10 + Math.sqrt(d.total || 1) * 3));
  if (d.type === "service_group") return Math.max(14, Math.min(30, 8 + Math.sqrt(d.total || 1) * 2.5));
  return 8;
}}

// ═══════════════════════════════════════════════════════════════
// Build visible graph from expanded state
// ═══════════════════════════════════════════════════════════════
function buildGraph() {{
  const nodes = [];
  const links = [];
  const visibleIds = new Set();

  // Always show root
  const rootData = nodeMap["root"];
  nodes.push({{ ...rootData, fx: 0, fy: 0 }});
  visibleIds.add("root");

  // Show modules and service groups (respect tfOnlyMode filter)
  DATA.nodes.forEach(n => {{
    if (n.type === "module") {{
      const expanded = expandedGroups.has(n.id);
      nodes.push({{ ...n, expanded }});
      visibleIds.add(n.id);
    }} else if (n.type === "service_group" && !tfOnlyMode) {{
      const expanded = expandedGroups.has(n.id);
      nodes.push({{ ...n, expanded }});
      visibleIds.add(n.id);
    }}
  }});

  // Links from root to modules/groups
  DATA.links.forEach(l => {{
    if ((l.link_type === "root-module" || l.link_type === "root-group")
        && visibleIds.has(l.source) && visibleIds.has(l.target)) {{
      links.push({{ ...l }});
    }}
  }});

  // Expanded groups: show their child resources
  expandedGroups.forEach(gid => {{
    const children = groupChildren[gid] || [];
    children.forEach(childArn => {{
      const childNode = nodeMap[childArn];
      if (childNode) {{
        nodes.push({{ ...childNode, parentGroup: gid }});
        visibleIds.add(childArn);
        links.push({{ source: gid, target: childArn,
                      link_type: childNode.managed ? "module-resource" : "group-resource" }});
      }}
    }});
  }});

  return {{ nodes, links }};
}}

// ═══════════════════════════════════════════════════════════════
// Particle animation
// ═══════════════════════════════════════════════════════════════
let particles = [];
let particleTimer = 0;

function spawnParticle() {{
  if (currentLinks.length === 0) return;
  const link = currentLinks[Math.floor(Math.random() * currentLinks.length)];
  if (!link.source || !link.target || link.source.x == null) return;
  particles.push({{
    link, t: 0, speed: 0.006 + Math.random() * 0.005,
    color: link.link_type === "root-module" ? "rgba(59,130,246,0.5)"
         : link.link_type === "root-group" ? "rgba(167,139,250,0.4)"
         : "rgba(148,163,184,0.3)",
    size: link.link_type && link.link_type.startsWith("root") ? 2 : 1.2,
  }});
}}

function updateParticles() {{
  particleTimer++;
  if (particleTimer % 4 === 0) spawnParticle();
  particleLayer.selectAll("circle").remove();
  particles = particles.filter(p => p.t < 1);
  particles.forEach(p => {{
    p.t += p.speed;
    if (p.link.source.x == null) return;
    const x = p.link.source.x + (p.link.target.x - p.link.source.x) * p.t;
    const y = p.link.source.y + (p.link.target.y - p.link.source.y) * p.t;
    particleLayer.append("circle")
      .attr("cx", x).attr("cy", y).attr("r", p.size)
      .attr("fill", p.color).attr("opacity", 1 - p.t * 0.5);
  }});
}}

// ═══════════════════════════════════════════════════════════════
// Render
// ═══════════════════════════════════════════════════════════════
function updateGraph() {{
  const graph = buildGraph();
  currentNodes = graph.nodes;
  currentLinks = graph.links;

  // --- Links ---
  const link = linkLayer.selectAll(".link").data(graph.links,
    d => (d.source.id||d.source) + "-" + (d.target.id||d.target));
  link.exit().transition().duration(300).attr("stroke-opacity", 0).remove();
  const linkEnter = link.enter().append("line").attr("class", "link")
    .attr("stroke", d => {{
      if (d.link_type === "root-module") return "rgba(59,130,246,0.12)";
      if (d.link_type === "root-group") return "rgba(167,139,250,0.10)";
      if (d.link_type === "group-resource") return "rgba(167,139,250,0.06)";
      return "rgba(59,130,246,0.06)";
    }})
    .attr("stroke-opacity", 0)
    .attr("stroke-width", d => d.link_type && d.link_type.startsWith("root") ? 1.2 : 0.5)
    .attr("stroke-dasharray", d => d.link_type === "group-resource" ? "3,3" : "none");
  linkEnter.transition().duration(500).attr("stroke-opacity", 1);
  const allLinks = linkEnter.merge(link);

  // --- Nodes ---
  const node = nodeLayer.selectAll(".node-g").data(graph.nodes, d => d.id);
  node.exit().transition().duration(300).attr("opacity", 0).remove();

  const nodeEnter = node.enter().append("g").attr("class", "node-g")
    .attr("cursor", d => (d.type === "module" || d.type === "service_group" || d.type === "resource") ? "pointer" : "default")
    .on("click", handleClick)
    .on("dblclick", handleDblClick)
    .on("mouseover", (e,d) => showTooltip(e,d))
    .on("mousemove", (e) => moveTooltip(e))
    .on("mouseout", () => hideTooltip())
    .call(d3.drag().on("start",dragStart).on("drag",dragging).on("end",dragEnd));

  // Filled circle for managed resources + all group nodes
  nodeEnter.filter(d => d.type !== "resource" || d.managed)
    .append("circle")
    .attr("r", 0)
    .attr("fill", nodeColor)
    .attr("filter", glowId)
    .attr("stroke", d => d.type === "module" || d.type === "service_group" ? "rgba(255,255,255,0.08)" : "none")
    .attr("stroke-width", d => d.type === "module" || d.type === "service_group" ? 1.5 : 0)
    .transition().duration(500).ease(d3.easeElasticOut.amplitude(1).period(0.6))
    .attr("r", nodeRadius);

  // Ring circle for unmanaged resources — transparent fill for full-area hover
  nodeEnter.filter(d => d.type === "resource" && !d.managed)
    .append("circle")
    .attr("r", 0)
    .attr("fill", d => {{
      const c = nodeColor(d);
      // Transparent fill: visible enough to see but ring stroke is dominant
      return c === "#22c55e" ? "rgba(34,197,94,0.12)" :
             c === "#ef4444" ? "rgba(239,68,68,0.15)" : "rgba(107,114,128,0.08)";
    }})
    .attr("stroke", nodeColor)
    .attr("stroke-width", 2.5)
    .attr("filter", glowId)
    .transition().duration(500).ease(d3.easeElasticOut.amplitude(1).period(0.6))
    .attr("r", nodeRadius);

  const allNodes = nodeEnter.merge(node);

  // --- Compliance rings on modules/groups ---
  ringLayer.selectAll(".ring-group").remove();
  graph.nodes.filter(d => (d.type === "module" || d.type === "service_group") && d.total > 0).forEach(d => {{
    const r = nodeRadius(d) + 4;
    const pct = (d.pct || 100) / 100;
    const rg = ringLayer.append("g").attr("class","ring-group").datum(d);
    rg.append("circle").attr("r", r)
      .attr("fill","none").attr("stroke","rgba(255,255,255,0.03)").attr("stroke-width", 2);
    if (pct > 0 && pct < 1) {{
      const arc = d3.arc().innerRadius(r - 1).outerRadius(r + 1)
        .startAngle(0).endAngle(pct * 2 * Math.PI);
      rg.append("path").attr("d", arc())
        .attr("fill", nodeColor(d)).attr("opacity", 0.6);
    }}
    rg.append("text").attr("dy", -nodeRadius(d) - 9)
      .attr("text-anchor","middle").attr("font-size","8px")
      .attr("font-family","'SFMono-Regular',Consolas,monospace")
      .attr("fill","rgba(255,255,255,0.2)").attr("filter","url(#text-shadow)")
      .text(d.total);
  }});

  // --- Labels on modules/groups/root ---
  labelLayer.selectAll(".node-label").remove();
  graph.nodes.filter(d => d.type !== "resource").forEach(d => {{
    labelLayer.append("text").attr("class","node-label")
      .attr("data-id", d.id)
      .attr("text-anchor","middle")
      .attr("dy", nodeRadius(d) + 16)
      .attr("font-size", d.type === "root" ? "13px" : "9px")
      .attr("font-family","'SFMono-Regular',Consolas,monospace")
      .attr("fill", d.type === "root" ? "#3b82f6" : d.type === "module" ? "rgba(148,163,184,0.6)" : "rgba(167,139,250,0.5)")
      .attr("font-weight", d.type === "root" ? "700" : "400")
      .attr("filter","url(#text-shadow)")
      .attr("pointer-events","none")
      .text(d.name ? d.name.replace("module.", "") : "")
      .datum(d);
  }});

  // --- Simulation ---
  if (simulation) simulation.stop();

  // Two-cluster layout: TF modules pulled left, service groups pulled right
  const clusterSpread = 200;

  simulation = d3.forceSimulation(graph.nodes)
    .force("link", d3.forceLink(graph.links).id(d => d.id)
      .distance(d => {{
        if (d.link_type === "root-module") return 180;
        if (d.link_type === "root-group") return 180;
        if (d.link_type === "module-resource") return 60;
        if (d.link_type === "group-resource") return 55;
        return 150;
      }})
      .strength(d => {{
        if (d.link_type === "root-module") return 0.25;
        if (d.link_type === "root-group") return 0.2;
        if (d.link_type === "module-resource") return 0.5;
        if (d.link_type === "group-resource") return 0.45;
        return 0.1;
      }}))
    .force("charge", d3.forceManyBody().strength(d => {{
      if (d.type === "root") return -1000;
      if (d.type === "module") return d.expanded ? -500 : -180;
      if (d.type === "service_group") return d.expanded ? -350 : -130;
      return -60;
    }}))
    .force("collision", d3.forceCollide().radius(d => nodeRadius(d) + (d.type === "resource" ? 10 : 16)))
    .force("clusterX", d3.forceX(d => {{
      if (d.type === "root") return 0;
      if (d.type === "module") return -clusterSpread;
      if (d.type === "service_group") return clusterSpread;
      // Resources follow their parent group's cluster
      if (d.parentGroup) {{
        const parent = nodeMap[d.parentGroup];
        if (parent && parent.type === "service_group") return clusterSpread;
        return -clusterSpread;
      }}
      return 0;
    }}).strength(d => d.type === "root" ? 0.15 : 0.04))
    .force("y", d3.forceY(0).strength(0.03))
    .alphaDecay(0.04)
    .velocityDecay(0.55)
    .on("tick", () => {{
      allLinks.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
              .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
      allNodes.attr("transform", d => `translate(${{d.x||0}},${{d.y||0}})`);
      ringLayer.selectAll(".ring-group")
        .attr("transform", function() {{ const d=d3.select(this).datum(); return d?`translate(${{d.x}},${{d.y}})`:"";}});
      labelLayer.selectAll(".node-label")
        .attr("x", function(){{ const d=d3.select(this).datum(); return d?d.x:0; }})
        .attr("y", function(){{ const d=d3.select(this).datum(); return d?d.y:0; }});
      updateParticles();
      updateActionPositions();
    }});
}}

// ═══════════════════════════════════════════════════════════════
// Interactions
// ═══════════════════════════════════════════════════════════════
// ── Action squares: twin sub-nodes that appear on resource click ──
const actionLayer = g.append("g").attr("class", "action-squares");
let activeActionNode = null;

function clearActions() {{
  actionLayer.selectAll("*").remove();
  activeActionNode = null;
}}

function showActionSquares(d) {{
  clearActions();
  activeActionNode = d.id;
  const hasConsole = !!d.console_url;
  const hasCure = !d.compliant && !!d.arn;
  if (!hasConsole && !hasCure) return;

  const sq = 18;  // square half-size
  const gap = 28; // distance from node center
  const r = nodeRadius(d);
  const items = [];

  if (hasConsole) items.push({{
    label: "AWS", url: d.console_url,
    color: "#3b82f6", hoverColor: "#60a5fa"
  }});
  if (hasCure) items.push({{
    label: "CURE", url: "/cure?arn=" + encodeURIComponent(d.arn),
    color: "#f59e0b", hoverColor: "#fbbf24"
  }});

  // Position: if two items, spread left/right; if one, just right
  items.forEach((item, i) => {{
    let offX;
    if (items.length === 2) {{
      offX = i === 0 ? -(r + gap + sq) : (r + gap + sq);
    }} else {{
      offX = r + gap + sq;
    }}
    const offY = 0;
    const ag = actionLayer.append("g")
      .attr("transform", `translate(${{(d.x||0) + offX}}, ${{(d.y||0) + offY}})`)
      .attr("cursor", "pointer")
      .attr("opacity", 0)
      .datum({{ parentNode: d }});

    // Connecting line from resource to square
    actionLayer.append("line")
      .attr("class", "action-link")
      .attr("x1", d.x || 0).attr("y1", d.y || 0)
      .attr("x2", (d.x||0) + offX).attr("y2", (d.y||0) + offY)
      .attr("stroke", item.color).attr("stroke-opacity", 0.25)
      .attr("stroke-width", 1.5).attr("stroke-dasharray", "4,3")
      .attr("opacity", 0)
      .transition().duration(300).attr("opacity", 1);

    // Square background
    ag.append("rect")
      .attr("x", -sq).attr("y", -sq)
      .attr("width", sq*2).attr("height", sq*2)
      .attr("rx", 4).attr("ry", 4)
      .attr("fill", item.color).attr("fill-opacity", 0.15)
      .attr("stroke", item.color).attr("stroke-width", 1.5)
      .attr("stroke-opacity", 0.6);

    // Label
    ag.append("text")
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("font-size", "10px").attr("font-weight", "700")
      .attr("font-family", "'SFMono-Regular',Consolas,monospace")
      .attr("fill", item.color).attr("pointer-events", "none")
      .text(item.label);

    // Hover effect
    ag.on("mouseover", function() {{
      d3.select(this).select("rect")
        .attr("fill-opacity", 0.3).attr("stroke-opacity", 1);
    }}).on("mouseout", function() {{
      d3.select(this).select("rect")
        .attr("fill-opacity", 0.15).attr("stroke-opacity", 0.6);
    }});

    // Click handler
    ag.on("click", function(event) {{
      event.stopPropagation();
      safeOpen(item.url);
    }});

    ag.transition().duration(300).delay(i * 80).attr("opacity", 1);
  }});
}}

// Update action square positions during simulation tick
function updateActionPositions() {{
  if (!activeActionNode) return;
  const d = currentNodes.find(n => n.id === activeActionNode);
  if (!d || d.x == null) {{ clearActions(); return; }}

  const hasConsole = !!d.console_url;
  const hasCure = !d.compliant && !!d.arn;
  const count = (hasConsole ? 1 : 0) + (hasCure ? 1 : 0);
  const r = nodeRadius(d);
  const sq = 18, gap = 28;
  let idx = 0;

  actionLayer.selectAll("g").each(function(_, i) {{
    let offX;
    if (count === 2) {{
      offX = i === 0 ? -(r + gap + sq) : (r + gap + sq);
    }} else {{
      offX = r + gap + sq;
    }}
    d3.select(this).attr("transform", `translate(${{d.x + offX}}, ${{d.y}})`);
  }});
  const lines = actionLayer.selectAll(".action-link");
  lines.attr("x1", d.x).attr("y1", d.y);
  lines.each(function(_, i) {{
    let offX;
    if (count === 2) {{
      offX = i === 0 ? -(r + gap + sq) : (r + gap + sq);
    }} else {{
      offX = r + gap + sq;
    }}
    d3.select(this).attr("x2", d.x + offX).attr("y2", d.y);
  }});
}}

// Close action squares when clicking background
svg.on("click.actions", clearActions);

function handleClick(event, d) {{
  event.stopPropagation();
  if (d.type === "module" || d.type === "service_group") {{
    clearActions();
    if (expandedGroups.has(d.id)) expandedGroups.delete(d.id);
    else expandedGroups.add(d.id);
    updateGraph();
  }} else if (d.type === "resource") {{
    const hasConsole = !!d.console_url;
    const hasCure = !d.compliant && !!d.arn;
    if (hasConsole && hasCure) {{
      // Both available — show action squares to choose
      if (activeActionNode === d.id) {{
        clearActions();  // toggle off
      }} else {{
        showActionSquares(d);
      }}
    }} else if (hasConsole) {{
      clearActions();
      safeOpen(d.console_url);
    }} else if (hasCure) {{
      clearActions();
      window.open("/cure?arn=" + encodeURIComponent(d.arn), "_blank");
    }}
  }}
}}

function handleDblClick(event, d) {{
  event.stopPropagation();
  if (d.type === "resource") {{
    // Double-click resource: open AWS Console (or CURE page)
    if (d.console_url) {{
      safeOpen(d.console_url);
    }} else if (!d.compliant && d.arn) {{
      window.open("/cure?arn=" + encodeURIComponent(d.arn), "_blank");
    }}
  }} else {{
    const scale = d.type === "root" ? 0.8 : 2.5;
    svg.transition().duration(750).ease(d3.easeCubicInOut)
      .call(zoomBehavior.transform,
        d3.zoomIdentity.translate(W/2 - (d.x||0)*scale, H/2 - (d.y||0)*scale).scale(scale));
  }}
}}

function dragStart(e, d) {{
  if (!e.active) simulation.alphaTarget(0.3).restart();
  d.fx = d.x; d.fy = d.y;
}}
function dragging(e, d) {{ d.fx = e.x; d.fy = e.y; }}
function dragEnd(e, d) {{
  if (!e.active) simulation.alphaTarget(0);
  if (d.type !== "root") {{ d.fx = null; d.fy = null; }}
}}

// ═══════════════════════════════════════════════════════════════
// Tooltip
// ═══════════════════════════════════════════════════════════════
function safeOpen(u) {{ if (/^(https?:\\/\\/|\\/(?!\\/))/i.test(String(u))) window.open(u, "_blank"); }}
function escHtml(s) {{ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }}

function showTooltip(event, d) {{
  let h = "";
  if (d.type === "root") {{
    h = `<div class="tt-title">${{escHtml(d.name)}}</div>
      <div class="tt-type">account root</div>
      <div class="tt-row"><span class="tt-label">Total Resources</span><span class="tt-value">${{DATA.stats.total}}</span></div>
      <div class="tt-row"><span class="tt-label">TF Managed</span><span class="tt-value" style="color:#3b82f6">${{DATA.stats.managed}}</span></div>
      <div class="tt-row"><span class="tt-label">Unmanaged</span><span class="tt-value" style="color:#a78bfa">${{DATA.stats.unmanaged}}</span></div>`;
    const ctKeys = Object.keys(commonTags);
    if (ctKeys.length > 0) {{
      h += `<div style="margin-top:8px;font-size:10px;color:#6b7280;">COMMON TAGS</div><div class="tt-tags">`;
      ctKeys.forEach(k => {{
        h += `<span class="tt-tag">${{escHtml(k)}}=${{escHtml(commonTags[k])}}</span>`;
      }});
      h += `</div>`;
    }}
  }} else if (d.type === "module") {{
    const pct = d.pct || 100;
    const nc = (d.total || 0) - (d.compliant || 0);
    const c = pct>=100?"#22c55e":pct>=70?"#f59e0b":"#ef4444";
    h = `<div class="tt-title">${{escHtml(d.name.replace("module.",""))}}</div>
      <div class="tt-type">terraform module</div>
      <div class="tt-row"><span class="tt-label">Resources</span><span class="tt-value">${{d.total}}</span></div>
      <div class="tt-row"><span class="tt-label">Compliant</span><span class="tt-value" style="color:#22c55e">${{d.compliant}}</span></div>
      <div class="tt-row"><span class="tt-label">Non-compliant</span><span class="tt-value" style="color:#ef4444">${{nc}}</span></div>`;
    if (d.file_path) h += `<div class="tt-row"><span class="tt-label">Source</span><span class="tt-value" style="font-size:10px">${{escHtml(d.file_path)}}</span></div>`;
    h += `<div class="tt-bar"><div class="tt-bar-fill" style="width:${{pct}}%;background:${{c}}"></div></div>
      <div style="margin-top:6px;font-size:10px;color:#4b5563">${{d.expanded?"Click to collapse":"Click to expand"}} &middot; Dblclick to zoom</div>`;
  }} else if (d.type === "service_group") {{
    const pct = d.pct || 100;
    const nc = (d.total || 0) - (d.compliant || 0);
    const c = pct>=100?"#22c55e":pct>=70?"#f59e0b":"#ef4444";
    h = `<div class="tt-title">${{escHtml(d.name)}}</div>
      <div class="tt-type">unmanaged service group</div>
      <div class="tt-row"><span class="tt-label">Resources</span><span class="tt-value">${{d.total}}</span></div>
      <div class="tt-row"><span class="tt-label">Compliant</span><span class="tt-value" style="color:#22c55e">${{d.compliant}}</span></div>
      <div class="tt-row"><span class="tt-label">Non-compliant</span><span class="tt-value" style="color:#ef4444">${{nc}}</span></div>
      <div class="tt-bar"><div class="tt-bar-fill" style="width:${{pct}}%;background:${{c}}"></div></div>
      <div style="margin-top:6px;font-size:10px;color:#4b5563">${{d.expanded?"Click to collapse":"Click to expand"}} &middot; Dblclick to zoom</div>`;
  }} else if (d.type === "resource") {{
    const statusColor = d.compliant ? "#22c55e" : "#ef4444";
    const statusText = d.compliant ? "Compliant" : "Non-compliant";
    const managedText = d.managed ? "TF Managed" : "Unmanaged";
    const managedColor = d.managed ? "#3b82f6" : "#a78bfa";
    h = `<div class="tt-title">${{escHtml(d.name || d.resource_type)}}</div>
      <div class="tt-type">${{escHtml(d.resource_type)}}</div>
      <div class="tt-row"><span class="tt-label">Status</span><span class="tt-value" style="color:${{statusColor}}">${{statusText}}</span></div>
      <div class="tt-row"><span class="tt-label">Managed</span><span class="tt-value" style="color:${{managedColor}}">${{managedText}}</span></div>`;
    if (d.tf_address) h += `<div class="tt-row"><span class="tt-label">TF Address</span><span class="tt-value" style="font-size:10px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{escHtml(d.tf_address)}}</span></div>`;
    if (d.region) h += `<div class="tt-row"><span class="tt-label">Region</span><span class="tt-value">${{escHtml(d.region)}}</span></div>`;
    if (d.cost != null && d.cost > 0) h += `<div class="tt-row"><span class="tt-label">Est. Cost</span><span class="tt-value">$${{Number(d.cost).toFixed(2)}}/mo</span></div>`;
    const ut = d.unique_tags || {{}};
    const utKeys = Object.keys(ut);
    if (utKeys.length > 0) {{
      h += `<div style="margin-top:6px;font-size:10px;color:#6b7280;">TAGS</div><div class="tt-tags">`;
      utKeys.slice(0, 8).forEach(k => {{
        h += `<span class="tt-tag">${{escHtml(k)}}=${{escHtml(ut[k])}}</span>`;
      }});
      if (utKeys.length > 8) h += `<span class="tt-tag">+${{utKeys.length - 8}} more</span>`;
      h += `</div>`;
    }}
    const destinations = [];
    if (d.console_url) destinations.push("AWS Console");
    if (!d.compliant && d.arn) destinations.push("CURE Page");
    if (destinations.length) h += `<div style="margin-top:6px;font-size:10px;color:#4b5563">Click &rarr; ${{destinations.join(" / ")}}</div>`;
  }}
  tooltip.html(h).classed("visible", true);
  moveTooltip(event);
}}

function moveTooltip(event) {{
  let x = event.clientX + 16, y = event.clientY - 10;
  const rect = tooltip.node().getBoundingClientRect();
  if (x + rect.width > W - 20) x = event.clientX - rect.width - 16;
  if (y + rect.height > H - 20) y = H - rect.height - 20;
  if (y < 60) y = 60;
  tooltip.style("left", x+"px").style("top", y+"px");
}}

function hideTooltip() {{ tooltip.classed("visible", false); }}

// ═══════════════════════════════════════════════════════════════
// Keyboard shortcuts
// ═══════════════════════════════════════════════════════════════
document.addEventListener("keydown", (e) => {{
  if (e.key === "r" || e.key === "R") {{
    svg.transition().duration(600).call(zoomBehavior.transform,
      d3.zoomIdentity.translate(W/2, H/2).scale(0.7));
  }}
  if (e.key === "f" || e.key === "F") {{
    fitToScreen();
  }}
  if (e.key === "a" || e.key === "A") {{
    const allGroups = DATA.nodes.filter(n => n.type === "module" || n.type === "service_group").map(n => n.id);
    if (expandedGroups.size === allGroups.length) {{
      expandedGroups.clear();
    }} else {{
      allGroups.forEach(id => expandedGroups.add(id));
    }}
    updateGraph();
    setTimeout(fitToScreen, 1200);
  }}
  if (e.key === "n" || e.key === "N") {{
    // Expand only non-compliant groups (pct < 100)
    const ncGroups = DATA.nodes.filter(n =>
      (n.type === "module" || n.type === "service_group") && (n.pct || 100) < 100
    ).map(n => n.id);
    const allExpanded = ncGroups.every(id => expandedGroups.has(id));
    if (allExpanded) {{
      ncGroups.forEach(id => expandedGroups.delete(id));
    }} else {{
      ncGroups.forEach(id => expandedGroups.add(id));
    }}
    updateGraph();
    setTimeout(fitToScreen, 1200);
  }}
  if (e.key === "t" || e.key === "T") {{
    window.toggleTfFilter();
  }}
}});

// ═══════════════════════════════════════════════════════════════
// Auto-fit: zoom to fit all nodes after simulation settles
// ═══════════════════════════════════════════════════════════════
function fitToScreen() {{
  if (currentNodes.length < 2) return;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  currentNodes.forEach(n => {{
    if (n.x == null) return;
    const r = nodeRadius(n);
    minX = Math.min(minX, n.x - r);
    maxX = Math.max(maxX, n.x + r);
    minY = Math.min(minY, n.y - r);
    maxY = Math.max(maxY, n.y + r);
  }});
  const gw = maxX - minX + 80, gh = maxY - minY + 80;
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  const scale = Math.min(0.95, Math.min((W - 48) / gw, (H - 100) / gh));
  svg.transition().duration(800).ease(d3.easeCubicInOut)
    .call(zoomBehavior.transform,
      d3.zoomIdentity.translate(W/2 - cx * scale, H/2 - cy * scale).scale(scale));
}}

// ═══════════════════════════════════════════════════════════════
// TF-only / All toggle
// ═══════════════════════════════════════════════════════════════
window.setFilter = function(tfOnly) {{
  tfOnlyMode = tfOnly;
  const btnAll = document.getElementById("btn-all");
  const btnTf = document.getElementById("btn-tf");
  if (tfOnlyMode) {{
    btnTf.style.background = "rgba(59,130,246,0.2)";
    btnTf.style.color = "#3b82f6";
    btnTf.style.fontWeight = "600";
    btnAll.style.background = "transparent";
    btnAll.style.color = "#6b7280";
    btnAll.style.fontWeight = "normal";
    // Collapse any expanded service groups
    expandedGroups.forEach(gid => {{
      const n = nodeMap[gid];
      if (n && n.type === "service_group") expandedGroups.delete(gid);
    }});
  }} else {{
    btnAll.style.background = "rgba(59,130,246,0.2)";
    btnAll.style.color = "#3b82f6";
    btnAll.style.fontWeight = "600";
    btnTf.style.background = "transparent";
    btnTf.style.color = "#6b7280";
    btnTf.style.fontWeight = "normal";
  }}
  updateGraph();
  setTimeout(fitToScreen, 800);
}};

// Keep T keyboard shortcut working (toggle between modes)
window.toggleTfFilter = function() {{
  setFilter(!tfOnlyMode);
}};

// ═══════════════════════════════════════════════════════════════
// Initialize
// ═══════════════════════════════════════════════════════════════
updateGraph();
// Auto-fit after initial layout stabilizes
setTimeout(fitToScreen, 2500);
</script>
</body>
</html>"""


def render_repo_network(slug, user_email=""):
    """Render a per-repo D3 force-directed network visualization."""
    from data_repo import get_repo_graph, get_repo_info

    graph = get_repo_graph(slug)
    info = get_repo_info(slug)
    repo_name = info.get("repo_slug", slug) if info else slug

    # The render_network function takes a graph dict with the same schema.
    # Override the root node name and stats label.
    for n in graph.get("nodes", []):
        if n.get("id") == "root":
            n["name"] = repo_name

    # Reuse the same full-page D3 renderer but with repo-scoped data.
    # We rebuild the render with repo-specific title.
    data_json = _js_json(graph, separators=(",", ":"))
    stats = graph.get("stats", {})

    import urllib.parse as _up

    base = f"/repos/{_up.quote(slug, safe='')}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(repo_name)} Network // __BRAND__</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #050508; color: #e2e8f0; font-family: 'SFMono-Regular', Consolas, monospace;
         overflow: hidden; }}
  {NAV_CSS_FIXED}
  canvas#bg {{ position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 0; }}
  svg {{ position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 1; }}
  .tooltip {{ position: fixed; pointer-events: none; background: rgba(8,8,12,0.94);
              backdrop-filter: blur(16px);
              border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 14px 18px;
              font-size: 12px; max-width: 420px; z-index: 200;
              box-shadow: 0 12px 48px rgba(0,0,0,0.6); opacity: 0; transition: opacity 0.12s; }}
  .tooltip.visible {{ opacity: 1; }}
  .tooltip .tt-title {{ font-weight: 700; font-size: 14px; margin-bottom: 3px; }}
  .tooltip .tt-type {{ color: #6b7280; font-size: 10px; text-transform: uppercase;
                       letter-spacing: 0.1em; margin-bottom: 8px; }}
  .tooltip .tt-row {{ display: flex; justify-content: space-between; gap: 16px; font-size: 11px;
                      padding: 2px 0; }}
  .tooltip .tt-label {{ color: #6b7280; }}
  .tooltip .tt-value {{ color: #e2e8f0; font-weight: 600; text-align: right; }}
  .tooltip .tt-tags {{ margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px; }}
  .tooltip .tt-tag {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08);
                      border-radius: 3px; padding: 1px 6px; font-size: 9px; color: #94a3b8; }}
  .legend {{ position: fixed; bottom: 24px; left: 24px; z-index: 90;
             background: rgba(8,8,12,0.85); backdrop-filter: blur(12px);
             border: 1px solid rgba(255,255,255,0.06); border-radius: 10px;
             padding: 14px 18px; font-size: 11px; }}
  .legend-title {{ font-weight: 700; margin-bottom: 6px; color: #94a3b8; font-size: 10px;
                   letter-spacing: 0.1em; }}
  .legend-row {{ display: flex; align-items: center; gap: 8px; margin: 5px 0; color: #6b7280; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  .hint {{ position: fixed; bottom: 24px; right: 24px; z-index: 90;
           color: #374151; font-size: 10px; text-align: right; line-height: 1.6; }}
  .hint kbd {{ background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06);
               border-radius: 3px; padding: 1px 5px; font-size: 9px; }}
</style>
</head>
<body>
{render_nav("repos", user_email)}
<div style="position:fixed;top:72px;left:50%;transform:translateX(-50%);z-index:90;
            display:flex;gap:12px;align-items:center;background:rgba(8,8,12,0.85);
            backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,0.06);
            border-radius:8px;padding:6px 16px;font-size:11px;">
  <a href="{base}" style="color:#6b7280;text-decoration:none;font-size:11px;">&larr; {_e(slug)}</a>
  <span style="color:#374151;">&middot;</span>
  <span style="color:#e2e8f0;font-weight:600;">{_e(repo_name)} Network</span>
  <span style="color:#374151;">&middot;</span>
  <span style="color:#6b7280;">{stats.get("total", 0)} resources</span>
</div>
<canvas id="bg"></canvas>
<svg id="canvas"></svg>
<div class="tooltip" id="tooltip"></div>
<div class="legend">
  <div class="legend-title">LEGEND</div>
  <div class="legend-row"><span class="legend-dot" style="background:#3b82f6;box-shadow:0 0 8px rgba(59,130,246,0.5);"></span> Service Group</div>
  <div class="legend-row"><span class="legend-dot" style="background:#22c55e;"></span> Compliant resource</div>
  <div class="legend-row"><span class="legend-dot" style="background:#ef4444;"></span> Non-compliant resource</div>
</div>
<div class="hint">
  <kbd>scroll</kbd> zoom &middot; <kbd>drag</kbd> pan<br>
  <kbd>click</kbd> group: expand &middot; <kbd>dblclick</kbd> zoom in<br>
  <kbd>A</kbd> all &middot; <kbd>F</kbd> fit &middot; <kbd>R</kbd> reset
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js" integrity="sha512-vc58qvvBdrDR4etbxMdlTt4GBQk1qjvyORR2nrsPsFPyrs+/u5c3+1Ct6upOgdZoIl7eq6k3a1UPDSNAQi/32A==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
const DATA = {data_json};
const W = window.innerWidth, H = window.innerHeight;

// Starfield
const bgCanvas = document.getElementById("bg");
bgCanvas.width = W; bgCanvas.height = H;
const bgCtx = bgCanvas.getContext("2d");
const stars = Array.from({{length: 120}}, () => ({{
  x: Math.random() * W, y: Math.random() * H,
  r: Math.random() * 1.2 + 0.2, a: Math.random() * 0.4 + 0.05,
  speed: Math.random() * 0.0003 + 0.0001
}}));
function drawStars(t) {{
  bgCtx.clearRect(0, 0, W, H);
  stars.forEach(s => {{
    const flicker = 0.5 + 0.5 * Math.sin(t * s.speed * 1000 + s.x);
    bgCtx.beginPath(); bgCtx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
    bgCtx.fillStyle = `rgba(148,163,184,${{s.a * flicker}})`; bgCtx.fill();
  }});
  requestAnimationFrame(drawStars);
}}
requestAnimationFrame(drawStars);

// SVG setup
const svg = d3.select("#canvas");
const tooltip = d3.select("#tooltip");
const g = svg.append("g");
const linkLayer = g.append("g");
const nodeLayer = g.append("g");
const labelLayer = g.append("g");

const zoomBehavior = d3.zoom().scaleExtent([0.08, 10])
  .on("zoom", (e) => g.attr("transform", e.transform));
svg.call(zoomBehavior);
svg.call(zoomBehavior.transform, d3.zoomIdentity.translate(W/2, H/2).scale(0.7));

// Glow filters
const defs = svg.append("defs");
[["glow-green","#22c55e"],["glow-red","#ef4444"],["glow-blue","#3b82f6"]].forEach(([id,color]) => {{
  const f = defs.append("filter").attr("id",id).attr("x","-80%").attr("y","-80%").attr("width","260%").attr("height","260%");
  f.append("feGaussianBlur").attr("stdDeviation","5").attr("result","blur");
  f.append("feFlood").attr("flood-color",color).attr("flood-opacity","0.3").attr("result","color");
  f.append("feComposite").attr("in","color").attr("in2","blur").attr("operator","in").attr("result","glow");
  const m = f.append("feMerge"); m.append("feMergeNode").attr("in","glow"); m.append("feMergeNode").attr("in","SourceGraphic");
}});

const nodeMap = {{}};
DATA.nodes.forEach(n => {{ nodeMap[n.id] = n; }});
const groupChildren = {{}};
DATA.links.forEach(l => {{
  if (l.link_type === "module-resource" || l.link_type === "root-module") {{
    if (l.link_type === "module-resource") {{
      if (!groupChildren[l.source]) groupChildren[l.source] = [];
      groupChildren[l.source].push(l.target);
    }}
  }}
}});

let expandedGroups = new Set();
let simulation;
let currentNodes = [], currentLinks = [];

function nodeColor(d) {{
  if (d.type === "root") return "#3b82f6";
  if (d.type === "resource") return d.compliant ? "#22c55e" : "#ef4444";
  if (d.type === "module") {{
    if (d.pct >= 100) return "#3b82f6";
    if (d.pct >= 70) return "#f59e0b";
    return "#ef4444";
  }}
  return "#6b7280";
}}
function glowId(d) {{
  if (d.type === "root") return "url(#glow-blue)";
  if (d.type === "resource") return d.compliant ? "url(#glow-green)" : "url(#glow-red)";
  if (d.type === "module") return "url(#glow-blue)";
  return "";
}}
function nodeRadius(d) {{
  if (d.type === "root") return 24;
  if (d.type === "resource") return 12;
  if (d.type === "module") return Math.max(14, Math.min(30, 8 + Math.sqrt(d.total || 1) * 3));
  return 8;
}}

function buildGraph() {{
  const nodes = [], links = [], visibleIds = new Set();
  const rootData = nodeMap["root"];
  nodes.push({{ ...rootData, fx: 0, fy: 0 }}); visibleIds.add("root");
  DATA.nodes.forEach(n => {{
    if (n.type === "module") {{
      nodes.push({{ ...n, expanded: expandedGroups.has(n.id) }}); visibleIds.add(n.id);
    }}
  }});
  DATA.links.forEach(l => {{
    if (l.link_type === "root-module" && visibleIds.has(l.source) && visibleIds.has(l.target))
      links.push({{ ...l }});
  }});
  expandedGroups.forEach(gid => {{
    (groupChildren[gid] || []).forEach(childArn => {{
      const childNode = nodeMap[childArn];
      if (childNode) {{
        nodes.push({{ ...childNode, parentGroup: gid }}); visibleIds.add(childArn);
        links.push({{ source: gid, target: childArn, link_type: "module-resource" }});
      }}
    }});
  }});
  return {{ nodes, links }};
}}

function updateGraph() {{
  const graph = buildGraph();
  currentNodes = graph.nodes; currentLinks = graph.links;
  const link = linkLayer.selectAll(".link").data(graph.links, d => (d.source.id||d.source)+"-"+(d.target.id||d.target));
  link.exit().transition().duration(300).attr("stroke-opacity",0).remove();
  const linkEnter = link.enter().append("line").attr("class","link")
    .attr("stroke", d => d.link_type === "root-module" ? "rgba(59,130,246,0.12)" : "rgba(59,130,246,0.06)")
    .attr("stroke-opacity",0).attr("stroke-width", d => d.link_type === "root-module" ? 1.2 : 0.5);
  linkEnter.transition().duration(500).attr("stroke-opacity",1);
  const allLinks = linkEnter.merge(link);

  const node = nodeLayer.selectAll(".node-g").data(graph.nodes, d => d.id);
  node.exit().transition().duration(300).attr("opacity",0).remove();
  const nodeEnter = node.enter().append("g").attr("class","node-g").attr("cursor","pointer")
    .on("click", handleClick).on("dblclick", handleDblClick)
    .on("mouseover", (e,d) => showTooltip(e,d)).on("mousemove", (e) => moveTooltip(e)).on("mouseout", hideTooltip)
    .call(d3.drag().on("start",dragStart).on("drag",dragging).on("end",dragEnd));
  nodeEnter.append("circle").attr("r",0).attr("fill",nodeColor).attr("filter",glowId)
    .attr("stroke", d => d.type === "module" ? "rgba(255,255,255,0.08)" : "none")
    .attr("stroke-width", d => d.type === "module" ? 1.5 : 0)
    .transition().duration(500).ease(d3.easeElasticOut.amplitude(1).period(0.6)).attr("r",nodeRadius);
  const allNodes = nodeEnter.merge(node);

  labelLayer.selectAll(".node-label").remove();
  graph.nodes.filter(d => d.type !== "resource").forEach(d => {{
    labelLayer.append("text").attr("class","node-label").attr("text-anchor","middle")
      .attr("dy", nodeRadius(d)+14).attr("font-size", d.type === "root" ? "12px" : "9px")
      .attr("font-family","'SFMono-Regular',Consolas,monospace")
      .attr("fill", d.type === "root" ? "#3b82f6" : "rgba(148,163,184,0.6)")
      .attr("font-weight", d.type === "root" ? "700" : "400")
      .attr("pointer-events","none").text(d.name || "").datum(d);
  }});

  if (simulation) simulation.stop();
  simulation = d3.forceSimulation(graph.nodes)
    .force("link", d3.forceLink(graph.links).id(d => d.id).distance(d => d.link_type === "root-module" ? 150 : 55).strength(d => d.link_type === "root-module" ? 0.25 : 0.5))
    .force("charge", d3.forceManyBody().strength(d => d.type === "root" ? -600 : d.type === "module" ? (d.expanded ? -350 : -150) : -50))
    .force("collision", d3.forceCollide().radius(d => nodeRadius(d) + 10))
    .force("y", d3.forceY(0).strength(0.03))
    .alphaDecay(0.04).velocityDecay(0.55)
    .on("tick", () => {{
      allLinks.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y).attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
      allNodes.attr("transform", d => `translate(${{d.x||0}},${{d.y||0}})`);
      labelLayer.selectAll(".node-label")
        .attr("x", function(){{ const d=d3.select(this).datum(); return d?d.x:0; }})
        .attr("y", function(){{ const d=d3.select(this).datum(); return d?d.y:0; }});
    }});
}}

function handleClick(event, d) {{
  event.stopPropagation();
  if (d.type === "module") {{
    if (expandedGroups.has(d.id)) expandedGroups.delete(d.id); else expandedGroups.add(d.id);
    updateGraph();
  }} else if (d.type === "resource" && d.console_url) {{
    safeOpen(d.console_url);
  }}
}}
function handleDblClick(event, d) {{
  event.stopPropagation();
  const scale = d.type === "root" ? 0.8 : 2.5;
  svg.transition().duration(750).call(zoomBehavior.transform,
    d3.zoomIdentity.translate(W/2-(d.x||0)*scale, H/2-(d.y||0)*scale).scale(scale));
}}
function dragStart(e,d) {{ if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; }}
function dragging(e,d) {{ d.fx=e.x; d.fy=e.y; }}
function dragEnd(e,d) {{ if (!e.active) simulation.alphaTarget(0); if (d.type !== "root") {{ d.fx=null; d.fy=null; }} }}

function safeOpen(u) {{ if (/^(https?:\\/\\/|\\/(?!\\/))/i.test(String(u))) window.open(u, "_blank"); }}
function esc(s) {{ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }}
function showTooltip(event, d) {{
  let h = "";
  if (d.type === "root") {{
    h = `<div class="tt-title">${{esc(d.name)}}</div><div class="tt-type">repository root</div>
      <div class="tt-row"><span class="tt-label">Total Resources</span><span class="tt-value">${{DATA.stats.total}}</span></div>`;
  }} else if (d.type === "module") {{
    const nc = (d.total||0)-(d.compliant||0);
    h = `<div class="tt-title">${{esc(d.name)}}</div><div class="tt-type">service group</div>
      <div class="tt-row"><span class="tt-label">Resources</span><span class="tt-value">${{d.total}}</span></div>
      <div class="tt-row"><span class="tt-label">Compliant</span><span class="tt-value" style="color:#22c55e">${{d.compliant}}</span></div>
      <div class="tt-row"><span class="tt-label">Non-compliant</span><span class="tt-value" style="color:#ef4444">${{nc}}</span></div>
      <div style="margin-top:6px;font-size:10px;color:#4b5563">${{d.expanded?"Click to collapse":"Click to expand"}}</div>`;
  }} else if (d.type === "resource") {{
    const sc = d.compliant ? "#22c55e" : "#ef4444";
    h = `<div class="tt-title">${{esc(d.name || d.resource_type)}}</div><div class="tt-type">${{esc(d.resource_type)}}</div>
      <div class="tt-row"><span class="tt-label">Status</span><span class="tt-value" style="color:${{sc}}">${{d.compliant?"Compliant":"Non-compliant"}}</span></div>`;
    if (d.region) h += `<div class="tt-row"><span class="tt-label">Region</span><span class="tt-value">${{esc(d.region)}}</span></div>`;
    const ut = d.unique_tags || {{}};
    const utk = Object.keys(ut);
    if (utk.length > 0) {{
      h += `<div style="margin-top:6px;font-size:10px;color:#6b7280;">TAGS</div><div class="tt-tags">`;
      utk.slice(0,6).forEach(k => {{ h += `<span class="tt-tag">${{esc(k)}}=${{esc(ut[k])}}</span>`; }});
      h += `</div>`;
    }}
    if (d.console_url) h += `<div style="margin-top:6px;font-size:10px;color:#4b5563">Click to open AWS Console</div>`;
  }}
  tooltip.html(h).classed("visible", true); moveTooltip(event);
}}
function moveTooltip(event) {{
  let x = event.clientX+16, y = event.clientY-10;
  const rect = tooltip.node().getBoundingClientRect();
  if (x+rect.width > W-20) x = event.clientX-rect.width-16;
  if (y+rect.height > H-20) y = H-rect.height-20;
  if (y < 60) y = 60;
  tooltip.style("left", x+"px").style("top", y+"px");
}}
function hideTooltip() {{ tooltip.classed("visible", false); }}

function fitToScreen() {{
  if (currentNodes.length < 2) return;
  let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
  currentNodes.forEach(n => {{
    if (n.x == null) return; const r = nodeRadius(n);
    minX=Math.min(minX,n.x-r); maxX=Math.max(maxX,n.x+r);
    minY=Math.min(minY,n.y-r); maxY=Math.max(maxY,n.y+r);
  }});
  const gw=maxX-minX+80, gh=maxY-minY+80;
  const cx=(minX+maxX)/2, cy=(minY+maxY)/2;
  const scale=Math.min(0.95, Math.min((W-48)/gw, (H-100)/gh));
  svg.transition().duration(800).call(zoomBehavior.transform,
    d3.zoomIdentity.translate(W/2-cx*scale, H/2-cy*scale).scale(scale));
}}

document.addEventListener("keydown", (e) => {{
  if (e.key==="r"||e.key==="R") svg.transition().duration(600).call(zoomBehavior.transform, d3.zoomIdentity.translate(W/2,H/2).scale(0.7));
  if (e.key==="f"||e.key==="F") fitToScreen();
  if (e.key==="a"||e.key==="A") {{
    const all = DATA.nodes.filter(n => n.type==="module").map(n => n.id);
    if (expandedGroups.size === all.length) expandedGroups.clear();
    else all.forEach(id => expandedGroups.add(id));
    updateGraph(); setTimeout(fitToScreen, 1200);
  }}
}});

updateGraph();
setTimeout(fitToScreen, 2500);
</script>
</body>
</html>"""
