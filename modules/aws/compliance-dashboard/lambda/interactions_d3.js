var DATA = __DATA_JSON__;
var EDGE_STYLES = __EDGE_STYLES_JSON__;
var FOCAL = DATA.focal;
var W = window.innerWidth, H = window.innerHeight;
var activeFilter = "all";
var highlightedNode = DATA.global ? null : FOCAL;

// ── Build filter pills from edge types ──
(function() {
  var bar = document.getElementById("filter-bar");
  var typesInGraph = {};
  DATA.edges.forEach(function(e) { typesInGraph[e.type] = true; });
  Object.keys(EDGE_STYLES).forEach(function(etype) {
    if (!typesInGraph[etype]) return;
    var s = EDGE_STYLES[etype];
    var btn = document.createElement("button");
    btn.className = "filter-pill";
    btn.setAttribute("data-type", etype);
    btn.textContent = s.label;
    btn.style.color = s.color;
    btn.style.borderColor = s.color + "33";
    btn.onclick = function() { setTypeFilter(etype); };
    bar.appendChild(btn);
  });
})();

function setTypeFilter(type) {
  activeFilter = type;
  document.querySelectorAll(".filter-pill").forEach(function(p) {
    if (p.getAttribute("data-type") === type) {
      p.classList.add("active");
      p.style.background = (type === "all" ? "rgba(255,255,255,0.08)" :
        EDGE_STYLES[type] ? EDGE_STYLES[type].color + "22" : "transparent");
    } else {
      p.classList.remove("active");
      p.style.background = "transparent";
    }
  });
  updateVisibility();
}

// ── Starfield ──
var bgCanvas = document.getElementById("bg");
bgCanvas.width = W; bgCanvas.height = H;
var bgCtx = bgCanvas.getContext("2d");
var stars = Array.from({length: 100}, function() {
  return {
    x: Math.random() * W, y: Math.random() * H,
    r: Math.random() * 1.0 + 0.2,
    a: Math.random() * 0.3 + 0.05,
    speed: Math.random() * 0.0003 + 0.0001
  };
});
function drawStars(t) {
  bgCtx.clearRect(0, 0, W, H);
  stars.forEach(function(s) {
    var flicker = 0.5 + 0.5 * Math.sin(t * s.speed * 1000 + s.x);
    bgCtx.beginPath();
    bgCtx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
    bgCtx.fillStyle = "rgba(148,163,184," + (s.a * flicker) + ")";
    bgCtx.fill();
  });
  requestAnimationFrame(drawStars);
}
requestAnimationFrame(drawStars);

// ── SVG setup ──
var svg = d3.select("#graph");
var tooltip = d3.select("#tooltip");
var g = svg.append("g");

var zoomBehavior = d3.zoom()
  .scaleExtent([0.2, 6])
  .on("zoom", function(e) { g.attr("transform", e.transform); });
svg.call(zoomBehavior);
svg.call(zoomBehavior.transform, d3.zoomIdentity.translate(W / 2, H / 2).scale(0.9));

// ── SVG defs (glow filters only — arrowheads drawn as polygons in tick) ──
var defs = svg.append("defs");

// Node glow filters
[["glow-blue","#3b82f6"],["glow-green","#22c55e"],["glow-purple","#a855f7"],
 ["glow-orange","#f97316"],["glow-red","#ef4444"],["glow-gray","#6b7280"],
 ["glow-focal","#e2e8f0"]].forEach(function(pair) {
  var f = defs.append("filter").attr("id", pair[0]).attr("x","-60%").attr("y","-60%").attr("width","220%").attr("height","220%");
  f.append("feGaussianBlur").attr("stdDeviation","4").attr("result","blur");
  f.append("feFlood").attr("flood-color",pair[1]).attr("flood-opacity","0.25").attr("result","color");
  f.append("feComposite").attr("in","color").attr("in2","blur").attr("operator","in").attr("result","glow");
  var m = f.append("feMerge");
  m.append("feMergeNode").attr("in","glow");
  m.append("feMergeNode").attr("in","SourceGraphic");
});

var zoneToGlow = {
  "apps": "url(#glow-blue)", "identity": "url(#glow-purple)",
  "serverless": "url(#glow-orange)", "public": "url(#glow-green)",
  "security": "url(#glow-red)", "infra": "url(#glow-gray)",
  "edge": "url(#glow-orange)"
};

// ── Build simulation ──
var linkLayer = g.append("g").attr("class", "links");
var arrowLayer = g.append("g").attr("class", "arrows");
var labelEdgeLayer = g.append("g").attr("class", "edge-labels");
var compLinkLayer = g.append("g").attr("class", "comp-links");
var nodeLayer = g.append("g").attr("class", "nodes");
var compNodeLayer = g.append("g").attr("class", "comp-nodes");
var labelLayer = g.append("g").attr("class", "labels");

var nodes = DATA.nodes.map(function(n) { return Object.assign({}, n); });
var edges = DATA.edges.map(function(e) { return Object.assign({}, e); });

var nodeRadius = function(d) {
  if (d.focal) return 40;
  var count = 0;
  edges.forEach(function(e) {
    var sid = typeof e.source === "object" ? e.source.id : e.source;
    var tid = typeof e.target === "object" ? e.target.id : e.target;
    if (sid === d.id || tid === d.id) count++;
  });
  return Math.max(18, Math.min(34, 14 + count * 3));
};

// ── Links ──
var linkSel = linkLayer.selectAll(".edge")
  .data(edges)
  .enter().append("path")
  .attr("class", "edge")
  .attr("fill", "none")
  .attr("stroke", function(d) { return (EDGE_STYLES[d.type] || EDGE_STYLES.http).color; })
  .attr("stroke-width", function(d) { var w = d.weight == null ? 1 : d.weight; return w === 2 ? 2.5 : w === 0 ? 1.0 : 1.8; })
  .attr("stroke-opacity", function(d) { return (d.weight === 0) ? 0.35 : 0.55; })
  .attr("stroke-dasharray", function(d) {
    var s = EDGE_STYLES[d.type] || EDGE_STYLES.http;
    return s.dash || "none";
  })
  .attr("data-type", function(d) { return d.type; })
  .style("pointer-events", "stroke")
  .style("cursor", "pointer")
  .on("mouseenter", function(event, d) {
    d3.select(this).attr("stroke-opacity", 0.9).attr("stroke-width", 3);
    showEdgeTooltip(event, d);
  })
  .on("mousemove", function(event) { moveTooltip(event); })
  .on("mouseleave", function(event, d) {
    var w = d.weight == null ? 1 : d.weight;
    d3.select(this).attr("stroke-opacity", w === 0 ? 0.35 : 0.55).attr("stroke-width", w === 2 ? 2.5 : w === 0 ? 1.0 : 1.8);
    hideTooltip();
  });

// ── Arrowheads (polygons, updated in tick) ──
var arrowSel = arrowLayer.selectAll(".arrow")
  .data(edges)
  .enter().append("polygon")
  .attr("class", "arrow")
  .attr("fill", function(d) { return (EDGE_STYLES[d.type] || EDGE_STYLES.http).color; })
  .attr("opacity", 0.7)
  .attr("data-type", function(d) { return d.type; });

// ── Nodes ──
var nodeSel = nodeLayer.selectAll(".node")
  .data(nodes)
  .enter().append("g")
  .attr("class", "node")
  .attr("cursor", "pointer")
  .call(d3.drag()
    .on("start", function(e, d) {
      if (!e.active) simulation.alphaTarget(0.3).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on("drag", function(e, d) { d.fx = e.x; d.fy = e.y; })
    .on("end", function(e, d) {
      if (!e.active) simulation.alphaTarget(0);
      d.fx = null; d.fy = null;
    }))
  .on("click", function(event, d) {
    event.stopPropagation();
    // Always collapse first, then decide whether to re-expand
    var wasExpanded = expandedNode === d.id;
    collapseComponents();
    if (wasExpanded) {
      // Toggle off — deselect
      highlightedNode = null;
    } else {
      // Select new node and expand
      highlightedNode = d.id;
      // Small delay so collapse positions settle before re-pushing
      setTimeout(function() { expandComponents(d); updateVisibility(); }, 50);
    }
    updateVisibility();
  })
  .on("dblclick", function(event, d) {
    event.stopPropagation();
    if (d.link) safeOpen(d.link);
  })
  .on("mouseenter", function(event, d) { showNodeTooltip(event, d); })
  .on("mousemove", function(event) { moveTooltip(event); })
  .on("mouseleave", function() { hideTooltip(); });

nodeSel.append("circle")
  .attr("r", nodeRadius)
  .attr("fill", function(d) { return d.focal ? "#e2e8f0" : d.color; })
  .attr("fill-opacity", function(d) { return d.focal ? 0.35 : 0.2; })
  .attr("stroke", function(d) { return d.focal ? "#e2e8f0" : d.color; })
  .attr("stroke-width", function(d) { return d.focal ? 3 : 2; })
  .attr("filter", function(d) { return d.focal ? "url(#glow-focal)" : (zoneToGlow[d.zone] || ""); });

// ── Node labels ──
var labelSel = labelLayer.selectAll(".node-label")
  .data(nodes)
  .enter().append("text")
  .attr("class", "node-label")
  .attr("text-anchor", "middle")
  .attr("dy", function(d) { return nodeRadius(d) + 14; })
  .attr("font-size", function(d) { return d.focal ? "12px" : "10px"; })
  .attr("font-weight", function(d) { return d.focal ? "700" : "400"; })
  .attr("font-family", "'SFMono-Regular',Consolas,monospace")
  .attr("fill", function(d) { return d.focal ? "#e2e8f0" : d.color; })
  .attr("fill-opacity", function(d) { return d.focal ? 1 : 0.7; })
  .attr("pointer-events", "none")
  .text(function(d) { return d.name; });

// ── Edge labels (shown mid-path) ──
var edgeLabelSel = labelEdgeLayer.selectAll(".edge-label")
  .data(edges)
  .enter().append("text")
  .attr("class", "edge-label")
  .attr("text-anchor", "middle")
  .attr("font-size", "8px")
  .attr("font-family", "'SFMono-Regular',Consolas,monospace")
  .attr("fill", function(d) { return (EDGE_STYLES[d.type] || EDGE_STYLES.http).color; })
  .attr("fill-opacity", 0.5)
  .attr("pointer-events", "none")
  .text(function(d) { return d.label; });

// ── Zone-based layout anchors (global mode) ──
// Injected from Python — tune in views_interactions.py _ZONE_ANCHORS / _NODE_ANCHORS
var ZONE_ANCHORS = __ZONE_ANCHORS_JSON__;
var NODE_ANCHORS = __NODE_ANCHORS_JSON__;

function zoneX(d) {
  if (!DATA.global) return 0;
  var na = NODE_ANCHORS[d.id];
  if (na) return na.x;
  var a = ZONE_ANCHORS[d.zone];
  return a ? a.x : 0;
}
function zoneY(d) {
  if (!DATA.global) return 0;
  var na = NODE_ANCHORS[d.id];
  if (na) return na.y;
  var a = ZONE_ANCHORS[d.zone];
  return a ? a.y : 0;
}

// Hub nodes (high-degree) get stronger repulsion to prevent clumping
var DEGREE = {};
edges.forEach(function(e) {
  var s = typeof e.source === "object" ? e.source.id : e.source;
  var t = typeof e.target === "object" ? e.target.id : e.target;
  DEGREE[s] = (DEGREE[s] || 0) + 1;
  DEGREE[t] = (DEGREE[t] || 0) + 1;
});
var HUB_DEGREE = 5;
function chargeStrength(d) {
  return (DEGREE[d.id] || 0) >= HUB_DEGREE ? -900 : -500;
}

// ── Simulation ──
var simulation = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(edges).id(function(d) { return d.id; })
    .distance(function(d) {
      var base = d.type === "auth" ? 100 : (d.type === "data" || d.type === "event") ? 160 : 130;
      var w = d.weight == null ? 1 : d.weight;
      return w === 2 ? base * 0.85 : w === 0 ? base * 1.2 : base;
    })
    .strength(0.5))
  .force("charge", d3.forceManyBody().strength(chargeStrength))
  .force("collision", d3.forceCollide().radius(function(d) { return nodeRadius(d) + 14; }))
  .force("x", d3.forceX(zoneX).strength(DATA.global ? 0.18 : 0.05))
  .force("y", d3.forceY(zoneY).strength(DATA.global ? 0.18 : 0.05))
  .alphaDecay(0.03)
  .velocityDecay(0.5)
  .on("tick", tick);

// Pin the focal node at center (per-service mode only)
if (!DATA.global) {
  nodes.forEach(function(n) {
    if (n.focal) { n.fx = 0; n.fy = 0; }
  });
}

function tick() {
  linkSel.attr("d", function(d) {
    var sx = d.source.x, sy = d.source.y;
    var tx = d.target.x, ty = d.target.y;
    var dx = tx - sx, dy = ty - sy;
    var dist = Math.sqrt(dx * dx + dy * dy) || 1;
    var sr = nodeRadius(d.source) + 4;
    var tr = nodeRadius(d.target) + 4;
    var x1 = sx + (dx / dist) * sr;
    var y1 = sy + (dy / dist) * sr;
    var x2 = tx - (dx / dist) * tr;
    var y2 = ty - (dy / dist) * tr;
    var curve = Math.min(dist * 0.15, 40);
    var nx = -dy / dist * curve;
    var ny = dx / dist * curve;
    var mx = (x1 + x2) / 2 + nx;
    var my = (y1 + y2) / 2 + ny;
    // Tangent at endpoint: direction from control to end
    var tdx = x2 - mx, tdy = y2 - my;
    var td = Math.sqrt(tdx * tdx + tdy * tdy) || 1;
    tdx /= td; tdy /= td;
    // Shorten path so line ends at arrowhead base
    var aLen = 9;
    var x2a = x2 - tdx * aLen, y2a = y2 - tdy * aLen;
    return "M" + x1 + "," + y1 + " Q" + mx + "," + my + " " + x2a + "," + y2a;
  });

  // Update arrowhead polygons
  arrowSel.attr("points", function(d) {
    var sx = d.source.x, sy = d.source.y;
    var tx = d.target.x, ty = d.target.y;
    var dx = tx - sx, dy = ty - sy;
    var dist = Math.sqrt(dx * dx + dy * dy) || 1;
    var sr = nodeRadius(d.source) + 4;
    var tr = nodeRadius(d.target) + 4;
    var x1 = sx + (dx / dist) * sr, y1 = sy + (dy / dist) * sr;
    var x2 = tx - (dx / dist) * tr, y2 = ty - (dy / dist) * tr;
    var curve = Math.min(dist * 0.15, 40);
    var nx = -dy / dist * curve, ny = dx / dist * curve;
    var mx = (x1 + x2) / 2 + nx, my = (y1 + y2) / 2 + ny;
    var tdx = x2 - mx, tdy = y2 - my;
    var td = Math.sqrt(tdx * tdx + tdy * tdy) || 1;
    tdx /= td; tdy /= td;
    var aLen = 9, pw = aLen * 0.38;
    var px = -tdy * pw, py = tdx * pw;
    return x2 + "," + y2 + " " +
      (x2 - tdx * aLen + px) + "," + (y2 - tdy * aLen + py) + " " +
      (x2 - tdx * aLen - px) + "," + (y2 - tdy * aLen - py);
  });

  nodeSel.attr("transform", function(d) { return "translate(" + d.x + "," + d.y + ")"; });
  labelSel.attr("x", function(d) { return d.x; }).attr("y", function(d) { return d.y; });

  edgeLabelSel
    .attr("x", function(d) {
      var dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
      var dist = Math.sqrt(dx * dx + dy * dy) || 1;
      var curve = Math.min(dist * 0.15, 40);
      return (d.source.x + d.target.x) / 2 + (-dy / dist * curve) * 0.5;
    })
    .attr("y", function(d) {
      var dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
      var dist = Math.sqrt(dx * dx + dy * dy) || 1;
      var curve = Math.min(dist * 0.15, 40);
      return (d.source.y + d.target.y) / 2 + (dx / dist * curve) * 0.5;
    });

  // Keep component children following their parent
  tickComponents();
}

// ── Visibility: type filter + node highlight + expanded dimming ──
// Hop-distance helpers: given a highlighted node, compute how far each
// other node is in the graph (1-hop = direct neighbor, 2-hop, etc.).
var _hopCache = {};
var _hopCacheKey = null;

function getHopDistance(nodeId) {
  // Returns 0 for the highlighted node itself, 1 for direct neighbors,
  // 2 for two-hop, Infinity for everything else.
  if (!highlightedNode) return Infinity;
  if (nodeId === highlightedNode) return 0;

  // Rebuild cache when highlighted node changes
  if (_hopCacheKey !== highlightedNode) {
    _hopCache = {};
    _hopCacheKey = highlightedNode;
    // BFS from highlighted node, max 2 hops
    var queue = [highlightedNode];
    _hopCache[highlightedNode] = 0;
    while (queue.length > 0) {
      var cur = queue.shift();
      var curDist = _hopCache[cur];
      if (curDist >= 2) continue;
      edges.forEach(function(e) {
        var sid = typeof e.source === "object" ? e.source.id : e.source;
        var tid = typeof e.target === "object" ? e.target.id : e.target;
        var neighbor = null;
        if (sid === cur) neighbor = tid;
        else if (tid === cur) neighbor = sid;
        if (neighbor !== null && _hopCache[neighbor] === undefined) {
          _hopCache[neighbor] = curDist + 1;
          queue.push(neighbor);
        }
      });
    }
  }
  return _hopCache[nodeId] !== undefined ? _hopCache[nodeId] : Infinity;
}

// How close is an edge to the highlighted node?
// 0 = directly connected, 1 = one hop away from a direct edge, etc.
function edgeProximity(d) {
  var sid = typeof d.source === "object" ? d.source.id : d.source;
  var tid = typeof d.target === "object" ? d.target.id : d.target;
  return Math.min(getHopDistance(sid), getHopDistance(tid));
}

function updateVisibility() {
  var expanded = !!expandedNode;
  if (_hopCacheKey !== highlightedNode) _hopCache = {};

  // ── Edges ──
  linkSel.attr("stroke-opacity", function(d) {
    var typeOk = activeFilter === "all" || d.type === activeFilter;
    if (!typeOk) return 0.06;
    if (!highlightedNode) return 0.55;
    var prox = edgeProximity(d);
    if (prox === 0) return expanded ? 0.5 : 0.85;
    if (!expanded) return 0.1;
    if (prox === 1) return 0.35;
    if (prox === 2) return 0.18;
    return 0.08;
  }).attr("stroke-width", function(d) {
    var w = d.weight == null ? 1 : d.weight;
    var base = w === 2 ? 2.5 : w === 0 ? 1.0 : 1.8;
    if (highlightedNode) {
      var prox = edgeProximity(d);
      if (prox === 0) return expanded ? base + 0.5 : base + 1;
    }
    return base;
  });

  // ── Arrowheads ──
  arrowSel.attr("opacity", function(d) {
    var typeOk = activeFilter === "all" || d.type === activeFilter;
    if (!typeOk) return 0.04;
    if (!highlightedNode) return 0.7;
    var prox = edgeProximity(d);
    if (prox === 0) return expanded ? 0.5 : 0.85;
    if (!expanded) return 0.08;
    if (prox === 1) return 0.35;
    if (prox === 2) return 0.15;
    return 0.06;
  });

  // ── Edge labels ──
  edgeLabelSel.attr("fill-opacity", function(d) {
    var typeOk = activeFilter === "all" || d.type === activeFilter;
    if (!typeOk) return 0.05;
    if (!highlightedNode) return 0.5;
    var prox = edgeProximity(d);
    if (prox === 0) return expanded ? 0.45 : 0.7;
    if (!expanded) return 0.08;
    if (prox === 1) return 0.3;
    if (prox === 2) return 0.15;
    return 0.06;
  });

  // ── Node circles (keep original colors, only vary opacity) ──
  nodeSel.selectAll("circle").attr("fill-opacity", function(d) {
    if (d.focal) return 0.35;
    if (!highlightedNode) return 0.2;
    if (d.id === highlightedNode) return expanded ? 0.5 : 0.4;
    var hop = getHopDistance(d.id);
    if (!expanded) return hop === 1 ? 0.3 : 0.08;
    if (hop === 1) return 0.2;
    if (hop === 2) return 0.12;
    return 0.06;
  }).attr("stroke-opacity", function(d) {
    if (d.focal) return 1;
    if (!highlightedNode) return 1;
    if (d.id === highlightedNode) return 1;
    var hop = getHopDistance(d.id);
    if (!expanded) return hop === 1 ? 1 : 0.2;
    if (hop === 1) return 0.6;
    if (hop === 2) return 0.3;
    return 0.12;
  });

  // ── Node labels (keep original colors, only vary opacity) ──
  labelSel.attr("fill-opacity", function(d) {
    if (d.focal) return 1;
    if (!highlightedNode) return 0.7;
    if (d.id === highlightedNode) return 1;
    var hop = getHopDistance(d.id);
    if (!expanded) return hop === 1 ? 0.8 : 0.2;
    if (hop === 1) return 0.6;
    if (hop === 2) return 0.3;
    return 0.1;
  });
}

// ── Component expansion (click-to-reveal infrastructure) ──
var expandedNode = null;
var compData = [];  // child node data for the expanded service
var savedPositions = {};  // original positions of pushed-out neighbors

// Initial highlight
updateVisibility();

// Find all neighbor node IDs for a given node
function getNeighborIds(nodeId) {
  var ids = {};
  edges.forEach(function(e) {
    var sid = typeof e.source === "object" ? e.source.id : e.source;
    var tid = typeof e.target === "object" ? e.target.id : e.target;
    if (sid === nodeId) ids[tid] = true;
    if (tid === nodeId) ids[sid] = true;
  });
  return ids;
}

function expandComponents(d) {
  if (!d.components || d.components.length === 0) return;
  expandedNode = d.id;

  var n = d.components.length;

  // ── Multi-ring honeycomb packing ──
  // Single ring for ≤9 children, honeycomb for more.
  // When multi-ring: distribute evenly across rings, stagger for honeycomb.
  var honeycombThreshold = 9;
  var baseRadius = 80;
  var ringGap = 42;
  var rings = [];

  if (n <= honeycombThreshold) {
    // Single ring — all children on one circle
    rings.push({ radius: baseRadius, count: n });
  } else {
    // Multi-ring: determine how many rings needed, then split evenly
    var numRings = Math.ceil(n / honeycombThreshold);
    var perRing = Math.ceil(n / numRings);
    var remaining = n;
    for (var ri = 0; ri < numRings; ri++) {
      var onRing = Math.min(perRing, remaining);
      rings.push({ radius: baseRadius + ri * ringGap, count: onRing });
      remaining -= onRing;
    }
  }

  var outerR = rings[rings.length - 1].radius;
  var pushDist = outerR + 80;

  // ── Push ALL nearby nodes outward to clear space ──
  savedPositions = {};
  var clearRadius = pushDist + 60;
  nodes.forEach(function(nd) {
    if (nd.id === d.id) return;
    var vx = nd.x - d.x, vy = nd.y - d.y;
    var vd = Math.sqrt(vx * vx + vy * vy) || 1;
    if (vd < clearRadius) {
      savedPositions[nd.id] = { x: nd.x, y: nd.y, fx: nd.fx, fy: nd.fy };
      nd.fx = d.x + (vx / vd) * (pushDist + 40);
      nd.fy = d.y + (vy / vd) * (pushDist + 40);
    }
  });
  simulation.alphaTarget(0.12).restart();
  setTimeout(function() { simulation.alphaTarget(0); }, 600);

  // ── Assign positions: even spacing per ring, staggered offset on odd rings ──
  var childPositions = [];  // { angle, radius } per component
  rings.forEach(function(ring, ri) {
    var offset = (ri % 2 === 1 && ring.count > 1) ? (Math.PI / ring.count) : 0;
    for (var ci = 0; ci < ring.count; ci++) {
      var angle = -Math.PI / 2 + offset + (2 * Math.PI * ci) / ring.count;
      childPositions.push({ angle: angle, radius: ring.radius });
    }
  });

  // ── Build component child data ──
  compData = d.components.map(function(c, i) {
    var pos = childPositions[i];
    return {
      id: d.id + "." + c.label,
      label: c.label + (c.count > 1 ? " x" + c.count : ""),
      shortLabel: c.label,
      count: c.count,
      color: c.color,
      desc: c.desc || "",
      submodule: c.submodule || "",
      parentId: d.id,
      parentName: d.name,
      angle: pos.angle,
      ringR: pos.radius,
      x: d.x,
      y: d.y,
      tx: d.x + Math.cos(pos.angle) * pos.radius,
      ty: d.y + Math.sin(pos.angle) * pos.radius,
    };
  });

  // ── Draw links from parent to each child ──
  compLinkLayer.selectAll(".comp-link")
    .data(compData)
    .enter().append("line")
    .attr("class", "comp-link")
    .attr("stroke", function(cd) { return cd.color; })
    .attr("stroke-width", 1)
    .attr("stroke-opacity", 0)
    .attr("stroke-dasharray", "3,2")
    .attr("x1", d.x).attr("y1", d.y)
    .attr("x2", d.x).attr("y2", d.y)
    .transition().duration(400).ease(d3.easeCubicOut)
    .attr("stroke-opacity", 0.5)
    .attr("x2", function(cd) { return cd.tx; })
    .attr("y2", function(cd) { return cd.ty; });

  // ── Draw child nodes ──
  var cg = compNodeLayer.selectAll(".comp-node")
    .data(compData)
    .enter().append("g")
    .attr("class", "comp-node")
    .attr("cursor", "default")
    .attr("transform", "translate(" + d.x + "," + d.y + ")")
    .style("opacity", 0)
    .on("mouseenter", function(event, cd) { showCompTooltip(event, cd); })
    .on("mousemove", function(event) { moveTooltip(event); })
    .on("mouseleave", function() { hideTooltip(); });

  cg.append("circle")
    .attr("r", 13)
    .attr("fill", function(cd) { return cd.color; })
    .attr("fill-opacity", 0.3)
    .attr("stroke", function(cd) { return cd.color; })
    .attr("stroke-width", 1.8)
    .attr("stroke-opacity", 0.85);

  cg.append("text")
    .attr("text-anchor", "middle")
    .attr("dy", 26)
    .attr("font-size", "9px")
    .attr("font-weight", "600")
    .attr("font-family", "'SFMono-Regular',Consolas,monospace")
    .attr("fill", function(cd) { return cd.color; })
    .attr("fill-opacity", 0.9)
    .attr("pointer-events", "none")
    .text(function(cd) { return cd.label; });

  // Animate outward
  cg.transition().duration(400).ease(d3.easeCubicOut)
    .attr("transform", function(cd) { return "translate(" + cd.tx + "," + cd.ty + ")"; })
    .style("opacity", 1);

  compData.forEach(function(cd) { cd.x = cd.tx; cd.y = cd.ty; });
}

function collapseComponents() {
  if (!expandedNode) return;
  // ── Restore pushed-out neighbors to their original positions ──
  nodes.forEach(function(nd) {
    var saved = savedPositions[nd.id];
    if (!saved) return;
    nd.x = saved.x;
    nd.y = saved.y;
    nd.fx = saved.fx;
    nd.fy = saved.fy;
  });
  savedPositions = {};

  expandedNode = null;
  compData = [];
  // Remove children immediately
  compNodeLayer.selectAll(".comp-node").remove();
  compLinkLayer.selectAll(".comp-link").remove();
  // Gentle reheat to settle
  simulation.alphaTarget(0.05).restart();
  setTimeout(function() { simulation.alphaTarget(0); }, 300);
}

// ── Component tooltip ──
function showCompTooltip(event, cd) {
  var h = '<div class="tt-title" style="color:' + escHtml(cd.color) + '">' + escHtml(cd.shortLabel) + '</div>';
  h += '<div class="tt-type">' + escHtml(cd.parentName);
  if (cd.submodule) h += ' / ' + escHtml(cd.submodule);
  h += '</div>';
  h += '<div class="tt-row"><span class="tt-label">Count</span><span class="tt-value">' + escHtml(cd.count) + ' resource' + (cd.count > 1 ? 's' : '') + '</span></div>';
  if (cd.desc) h += '<div style="margin-top:4px;font-size:11px;color:#94a3b8;">' + escHtml(cd.desc) + '</div>';
  tooltip.html(h).classed("visible", true);
  moveTooltip(event);
}

// Update component positions on tick (follow parent node)
function tickComponents() {
  if (!expandedNode || compData.length === 0) return;
  var parent = null;
  nodes.forEach(function(n) { if (n.id === expandedNode) parent = n; });
  if (!parent) return;

  compData.forEach(function(cd) {
    cd.x = parent.x + Math.cos(cd.angle) * cd.ringR;
    cd.y = parent.y + Math.sin(cd.angle) * cd.ringR;
  });

  compLinkLayer.selectAll(".comp-link")
    .attr("x1", parent.x).attr("y1", parent.y)
    .attr("x2", function(cd) { return cd.x; })
    .attr("y2", function(cd) { return cd.y; });

  compNodeLayer.selectAll(".comp-node")
    .attr("transform", function(cd) { return "translate(" + cd.x + "," + cd.y + ")"; });
}

// Click background to deselect and collapse
svg.on("click", function() {
  highlightedNode = (highlightedNode === null) ? FOCAL : null;
  collapseComponents();
  updateVisibility();
});

// ── Tooltips ──
function escHtml(s) { return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;"); }
function safeOpen(u) { if (/^(https?:\/\/|\/(?!\/))/i.test(String(u))) window.open(u, "_blank"); }

function showNodeTooltip(event, d) {
  var conns = [];
  edges.forEach(function(e) {
    var sid = typeof e.source === "object" ? e.source.id : e.source;
    var tid = typeof e.target === "object" ? e.target.id : e.target;
    if (sid === d.id) conns.push("&rarr; " + escHtml(tid) + " (" + escHtml(e.label || e.type) + ")");
    if (tid === d.id) conns.push("&larr; " + escHtml(sid) + " (" + escHtml(e.label || e.type) + ")");
  });
  var h = '<div class="tt-title">' + escHtml(d.name) + (d.focal ? ' <span style="color:#f59e0b;font-size:10px;">(focal)</span>' : '') + '</div>';
  h += '<div class="tt-type">' + escHtml(d.type || d.zone) + '</div>';
  if (d.host) h += '<div class="tt-row"><span class="tt-label">Host</span><span class="tt-value">' + escHtml(d.host) + '</span></div>';
  if (d.auth) h += '<div class="tt-row"><span class="tt-label">Auth</span><span class="tt-value" style="color:#a855f7">' + escHtml(d.auth) + '</span></div>';
  if (d.desc) h += '<div style="margin-top:4px;font-size:11px;color:#94a3b8;">' + escHtml(d.desc) + '</div>';
  if (conns.length > 0) {
    h += '<div style="margin-top:8px;font-size:10px;color:#4b5563;border-top:1px solid rgba(255,255,255,0.06);padding-top:6px;">';
    h += conns.join('<br>');
    h += '</div>';
  }
  if (d.components && d.components.length > 0) {
    h += '<div style="margin-top:6px;font-size:10px;color:#f59e0b;">Click to expand ' + d.components.length + ' infrastructure components</div>';
  } else {
    h += '<div style="margin-top:6px;font-size:10px;color:#374151;">Click to highlight &middot; Dblclick for detail</div>';
  }
  tooltip.html(h).classed("visible", true);
  moveTooltip(event);
}

function showEdgeTooltip(event, d) {
  var style = EDGE_STYLES[d.type] || EDGE_STYLES.http;
  var h = '<div class="tt-title" style="color:' + style.color + '">' + escHtml(d.label || style.label) + '</div>';
  var sid = typeof d.source === "object" ? d.source.id : d.source;
  var tid = typeof d.target === "object" ? d.target.id : d.target;
  h += '<div class="tt-row"><span class="tt-label">From</span><span class="tt-value">' + escHtml(sid) + '</span></div>';
  h += '<div class="tt-row"><span class="tt-label">To</span><span class="tt-value">' + escHtml(tid) + '</span></div>';
  h += '<div class="tt-row"><span class="tt-label">Type</span><span class="tt-value" style="color:' + style.color + '">' + escHtml(style.label) + '</span></div>';
  if (d.protocol) h += '<div class="tt-row"><span class="tt-label">Protocol</span><span class="tt-value">' + escHtml(d.protocol) + '</span></div>';
  if (d.async) h += '<div class="tt-row"><span class="tt-label">Mode</span><span class="tt-value" style="color:#f59e0b">Async</span></div>';
  tooltip.html(h).classed("visible", true);
  moveTooltip(event);
}

function moveTooltip(event) {
  var x = event.clientX + 16, y = event.clientY - 10;
  var rect = tooltip.node().getBoundingClientRect();
  if (x + rect.width > W - 20) x = event.clientX - rect.width - 16;
  if (y + rect.height > H - 20) y = H - rect.height - 20;
  if (y < 60) y = 60;
  tooltip.style("left", x + "px").style("top", y + "px");
}

function hideTooltip() { tooltip.classed("visible", false); }

// ── Keyboard ──
function fitToScreen() {
  if (nodes.length < 2) return;
  var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  nodes.forEach(function(n) {
    if (n.x == null) return;
    var r = nodeRadius(n);
    minX = Math.min(minX, n.x - r);
    maxX = Math.max(maxX, n.x + r);
    minY = Math.min(minY, n.y - r);
    maxY = Math.max(maxY, n.y + r);
  });
  var gw = maxX - minX + 80, gh = maxY - minY + 80;
  var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  var scale = Math.min(0.95, Math.min((W - 48) / gw, (H - 100) / gh));
  svg.transition().duration(800).ease(d3.easeCubicInOut)
    .call(zoomBehavior.transform,
      d3.zoomIdentity.translate(W / 2 - cx * scale, H / 2 - cy * scale).scale(scale));
}

document.addEventListener("keydown", function(e) {
  if (e.key === "r" || e.key === "R") {
    svg.transition().duration(600).call(zoomBehavior.transform,
      d3.zoomIdentity.translate(W / 2, H / 2).scale(0.9));
  }
  if (e.key === "f" || e.key === "F") { fitToScreen(); }
});

setTimeout(fitToScreen, 2000);
