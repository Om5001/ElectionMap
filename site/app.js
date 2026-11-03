/* Election Forest — front end. Reads data/forecast.json, data/model.json, data/history.json. */
(() => {
  const REFRESH = 60_000;
  const S = { fc: null, md: null, hist: [], office: "senate", sel: null, selState: null, lastGen: null };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (p, d = 0) => (p == null ? "–" : `${(p * 100).toFixed(d)}%`);
  const sgn = (m, d = 1) => (m == null ? "–" : `${m >= 0 ? "+" : ""}${m.toFixed(d)}`);
  const OFF = { senate: "Senate", house: "House", governor: "Governor" };
  const SRC = { kalshi: "Kalshi", polymarket: "Polymarket", predictit: "PredictIt", manifold: "Manifold" };

  let PAL = {};
  function palette() {
    const cs = getComputedStyle(document.documentElement);
    ["dem", "rep", "ind", "mid", "none", "ink", "muted", "live", "surface", "line"].forEach((k) => (PAL[k] = cs.getPropertyValue(`--${k}`).trim()));
  }
  const colorP = (p, ch = "D") => {
    if (p == null) return PAL.none;
    const hi = ch === "I" ? PAL.ind : PAL.dem;
    return p >= 0.5 ? d3.interpolateRgb(PAL.mid, hi)(Math.min(1, (p - 0.5) * 2.2)) : d3.interpolateRgb(PAL.mid, PAL.rep)(Math.min(1, (0.5 - p) * 2.2));
  };
  const party = (r) => r.challenger || "D";
  const label = (r) => r.office === "house" ? `${r.state}-${r.district}` : `${r.stateName} ${OFF[r.office]}${r.special ? " (special)" : ""}`;
  const cands = (r) => {
    const c = r.candidates || {};
    const a = c[party(r)], b = c.R;
    return a || b ? `${a ? `${esc(a)} (${party(r)})` : party(r)} vs ${b ? `${esc(b)} (R)` : "R"}` : "";
  };
  const leaderTxt = (r) => {
    const p = r.pChallenger, ch = party(r);
    return p >= 0.5 ? `<span class="${ch}-txt">${ch} ${pct(p)}</span>` : `<span class="R-txt">R ${pct(1 - p)}</span>`;
  };
  const byKey = (k) => S.fc.races.find((r) => r.key === k);

  // ------------------------------------------------------------------ load
  async function getJSON(u) { const r = await fetch(`${u}?t=${Date.now()}`, { cache: "no-store" }); if (!r.ok) throw new Error(`${u} ${r.status}`); return r.json(); }
  async function load(full = false) {
    try {
      const [fc, hist] = await Promise.all([getJSON("data/forecast.json"), getJSON("data/history.json").catch(() => [])]);
      if (full || !S.md) S.md = await getJSON("data/model.json");
      const changed = fc.generatedAt !== S.lastGen;
      S.fc = fc; S.hist = hist; S.lastGen = fc.generatedAt;
      if (!S.sel || !byKey(S.sel)) S.sel = fc.tracker[0];
      renderAll(changed);
    } catch (e) { $("updated").textContent = `Data unavailable (${e.message}) — retrying`; }
  }

  function renderAll(animate) {
    const fc = S.fc;
    $("mode").className = `mode ${fc.mode}`;
    $("mode").textContent = fc.mode === "live" ? "Live — votes counting" : "Pre-election";
    $("updated").textContent = `Updated ${new Date(fc.generatedAt).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`;
    const notes = [];
    if (fc.demo) notes.push("Demo build: prediction-market prices are synthetic test fixtures. Polls, economic data and the model are real; deploy the GitHub Action for live market feeds.");
    if (fc.simulatedNight) notes.push("Vote counts shown are SIMULATED to demonstrate election-night mode.");
    $("banner").hidden = !notes.length; $("banner").textContent = notes.join(" ");
    renderKPIs(); renderMacro(); renderMap(); renderTracker(); renderFeeds(); renderModel(animate);
  }

  // ------------------------------------------------------------------ strip
  function renderKPIs() {
    const fc = S.fc, sim = fc.sims || {};
    const sen = sim.senate || {}, gov = sim.governor || {}, house = sim.house || {};
    const hc = (fc.marketControl || []).find((c) => c.key === "control-house");
    const counting = fc.races.filter((r) => r.status === "counting").length, called = fc.races.filter((r) => r.status === "called").length;
    const k = (l, v, s) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
    $("kpis").innerHTML =
      k("Senate: Dem control", `<span class="D-txt">${pct(sen.pDemControl)}</span>`, `Avg. ${sen.expectedDemSeats?.toFixed(1) ?? "–"} D seats · need 51`) +
      k("House: Dem control", house.complete ? `<span class="D-txt">${pct(house.pDemControl)}</span>` : hc ? `<span class="D-txt">${pct(hc.probs.D)}</span>` : "–",
        house.complete ? "Simulated, all 435 seats" : `Market consensus · ${house.n ?? 0} districts modeled`) +
      k("Governors", `${gov.expectedChallenger?.toFixed(1) ?? "–"}`, `Expected D/I wins of ${gov.n ?? 36} races`) +
      k("Generic ballot", `<span class="D-txt">D ${sgn(fc.genericBallot)}</span>`, "National environment input") +
      k("Counting / called", `${counting} / ${called}`, fc.live.nResolved ? `National shift ${sgn(fc.live.nationalShift)} from ${fc.live.nResolved} decided` : "No races decided yet");
  }
  function renderMacro() {
    $("macro").innerHTML = (S.fc.macro || []).map((m) => {
      const ch = m.yoyPct != null ? `${m.yoyPct >= 0 ? "▲" : "▼"} ${Math.abs(m.yoyPct).toFixed(1)}% y/y` : m.yoyChg != null ? `${m.yoyChg >= 0 ? "▲" : "▼"} ${Math.abs(m.yoyChg).toFixed(2)} y/y` : "";
      const val = m.key === "cpi" ? m.display : m.value;
      return `<div class="mac" title="as of ${m.asOf}">${esc(m.label)}<b>${esc(val)}</b><span class="${(m.yoyChg ?? 0) > 0 ? "up" : "down"}">${ch}</span></div>`;
    }).join("");
  }

  // ------------------------------------------------------------------ map
  let svg, gS, path, feats;
  function stateRaces(abbr) { return S.fc.races.filter((r) => r.office === S.office && r.state === abbr); }
  function stateFill(abbr) {
    const rs = stateRaces(abbr);
    if (!rs.length) return { fill: PAL.none, rs };
    const p = d3.mean(rs, (r) => r.pChallenger);
    return { fill: colorP(p, rs.some((r) => party(r) === "I") ? "I" : "D"), rs, p };
  }
  function renderMap() {
    const fc = S.fc;
    if (!svg) {
      const W = 960, H = 590;
      svg = d3.select("#map").append("svg").attr("viewBox", `0 0 ${W} ${H}`);
      const fcoll = topojson.feature(window.__US, window.__US.objects.states);
      path = d3.geoPath(d3.geoAlbersUsa().fitSize([W, H], fcoll));
      const f2a = Object.fromEntries(Object.entries(fc.calendar.states).map(([a, s]) => [s.fips, a]));
      feats = fcoll.features.map((f) => Object.assign(f, { abbr: f2a[f.id] })).filter((f) => f.abbr);
      gS = svg.append("g");
      gS.selectAll("path").data(feats).join("path").attr("class", "st").attr("d", path)
        .on("mousemove", tipState).on("mouseleave", () => ($("tip").hidden = true))
        .on("click", (e, f) => clickState(f.abbr));
      svg.append("g").attr("class", "lbls").selectAll("text").data(feats.filter((f) => !"RI DE CT NJ MD MA VT NH DC".includes(f.abbr)))
        .join("text").attr("class", "lbl").attr("transform", (f) => `translate(${path.centroid(f)})`).attr("dy", ".35em").text((f) => f.abbr);
      svg.append("g").attr("class", "chks");
    }
    gS.selectAll("path").each(function (f) {
      const v = stateFill(f.abbr);
      const counting = v.rs.some((r) => r.status === "counting");
      d3.select(this).transition().duration(500).attr("fill", v.fill);
      d3.select(this).classed("norace", !v.rs.length).classed("counting", counting).classed("sel", S.selState === f.abbr || (byKey(S.sel)?.state === f.abbr && byKey(S.sel)?.office === S.office));
    });
    svg.selectAll("text.lbl").attr("fill", (f) => { const v = stateFill(f.abbr); return v.p != null && Math.abs(v.p - 0.5) > 0.3 ? "#fff" : PAL.ink; })
      .attr("dy", (f) => (stateFill(f.abbr).rs.every((r) => r.status === "called") && stateFill(f.abbr).rs.length ? "-.2em" : ".35em"));
    const called = S.office === "house" ? [] : feats.filter((f) => { const rs = stateRaces(f.abbr); return rs.length && rs.every((r) => r.status === "called"); });
    svg.select(".chks").selectAll("text").data(called, (f) => f.abbr).join("text").attr("class", "chk")
      .attr("transform", (f) => `translate(${path.centroid(f)})`).attr("dy", "1em").text("✓");

    $("mapLegend").innerHTML = `<span>R<span class="grad"></span>D</span><span style="color:var(--ind)">■ purple = independent challenger</span>` +
      `<span><span style="display:inline-block;width:12px;height:10px;border:1.5px dashed var(--live);vertical-align:middle"></span> counting</span><span>✓ called</span>` +
      (S.office !== "house" ? `<span><span style="display:inline-block;width:12px;height:10px;background:var(--none);border:1px solid var(--line);vertical-align:middle"></span> no race</span>` : "");
    renderChamber(); renderWaffle(); renderStateDetail();
  }
  function tipState(e, f) {
    const rs = stateRaces(f.abbr), name = S.fc.calendar.states[f.abbr].name;
    let h = `<b>${esc(name)}</b><br>`;
    if (!rs.length) h += S.office === "house" ? "No modeled districts" : `No ${OFF[S.office]} race in 2026`;
    else if (S.office === "house") h += `${rs.length} of ${S.fc.calendar.houseSeats[f.abbr]} districts modeled<br>avg P(D) ${pct(d3.mean(rs, (r) => r.pChallenger))}`;
    else h += rs.map((r) => `${r.special ? "Special · " : ""}${leaderTxt(r)} · margin ${sgn(r.margin)}${r.result?.pctIn ? ` · ${Math.round(r.result.pctIn)}% in` : ""}`).join("<br>");
    const t = $("tip"); t.innerHTML = h; t.hidden = false;
    t.style.left = `${Math.min(e.clientX + 14, innerWidth - 270)}px`; t.style.top = `${e.clientY + 14}px`;
  }
  function clickState(abbr) {
    const rs = stateRaces(abbr);
    if (!rs.length) return;
    if (S.office === "house") { S.selState = abbr; renderMap(); $("stateDetail").scrollIntoView({ block: "nearest", behavior: "smooth" }); }
    else select(rs[0].key);
  }
  function renderChamber() {
    const sim = S.fc.sims[S.office] || {}, fc = S.fc;
    const c = (l, v) => `<div class="ch"><div class="l">${l}</div><div class="v">${v}</div></div>`;
    let h = "";
    if (S.office === "senate") {
      h = c("P(Dem control)", `<span class="D-txt">${pct(sim.pDemControl)}</span>`) + c("P(GOP control)", `<span class="R-txt">${pct(sim.pRepControl)}</span>`) +
        c("Expected seats", `<span class="D-txt">${sim.expectedDemSeats?.toFixed(1)}</span> – <span class="R-txt">${sim.expectedRepSeats?.toFixed(1)}</span>`) +
        c("Not up in 2026", `${fc.senateMath.notUp.D} D · ${fc.senateMath.notUp.R} R`);
    } else if (S.office === "house") {
      const hc = (fc.marketControl || []).find((x) => x.key === "control-house");
      const rs = fc.races.filter((r) => r.office === "house");
      h = c("Markets: Dem control", hc ? `<span class="D-txt">${pct(hc.probs.D)}</span>` : "–") + c("Districts modeled", `${rs.length} / 435`) +
        c("Lean D (modeled)", `<span class="D-txt">${rs.filter((r) => r.pChallenger > .5).length}</span>`) + c("Lean R (modeled)", `<span class="R-txt">${rs.filter((r) => r.pChallenger <= .5).length}</span>`);
    } else {
      h = c("Races", sim.n ?? 0) + c("Expected D/I wins", `<span class="D-txt">${sim.expectedChallenger?.toFixed(1)}</span>`) +
        c("Toss-ups (<60%)", fc.races.filter((r) => r.office === "governor" && r.leaderProb < .6).length);
    }
    $("chamber").innerHTML = h;
  }
  function renderWaffle() {
    if (S.office !== "house") { $("waffle").innerHTML = ""; return; }
    const rs = S.fc.races.filter((r) => r.office === "house").sort((a, b) => b.pChallenger - a.pChallenger);
    $("waffle").innerHTML = `<h3>Modeled districts, most to least Democratic <span class="hint">click a square</span></h3><div class="cells">${
      rs.map((r) => `<span class="c" data-k="${r.key}" title="${esc(label(r))}: P(D) ${pct(r.pChallenger)}" style="background:${colorP(r.pChallenger)};${r.status === "counting" ? `outline:1.5px solid ${PAL.live}` : ""}"></span>`).join("")}</div>`;
    $("waffle").querySelectorAll(".c").forEach((el) => el.addEventListener("click", () => select(el.dataset.k)));
  }
  function renderStateDetail() {
    const ab = S.selState;
    if (S.office !== "house" || !ab) { $("stateDetail").innerHTML = ""; return; }
    const rs = stateRaces(ab).sort((a, b) => a.district.localeCompare(b.district));
    $("stateDetail").innerHTML = `<h3>${esc(S.fc.calendar.states[ab].name)} — modeled districts</h3>` +
      rs.map((r) => `<div class="sd-row" data-k="${r.key}"><span>${label(r)}</span><span>${leaderTxt(r)} · ${sgn(r.margin)}</span></div>`).join("");
    $("stateDetail").querySelectorAll(".sd-row").forEach((el) => el.addEventListener("click", () => select(el.dataset.k)));
  }

  // ------------------------------------------------------------------ tracker
  function spark(key) {
    const pts = S.hist.map((h) => h.races?.[key]).filter((v) => v != null);
    if (pts.length < 2) return "";
    const w = 90, h = 22, x = d3.scaleLinear([0, pts.length - 1], [1, w - 1]), y = d3.scaleLinear([0, 1], [h - 1, 1]);
    const line = d3.line((d, i) => x(i), (d) => y(d))(pts);
    return `<svg class="spark" width="${w}" height="${h}"><line x1="0" x2="${w}" y1="${y(.5)}" y2="${y(.5)}" stroke="${PAL.line}"/><path d="${line}" fill="none" stroke="${PAL.ink}" stroke-width="1.4"/></svg>`;
  }
  function renderTracker() {
    $("tracker").innerHTML = S.fc.tracker.map(byKey).filter(Boolean).map((r) => {
      const p = r.pChallenger, ch = party(r);
      const st = r.status === "counting" ? `<span class="badge counting">${Math.round(r.result.pctIn)}% in</span>` : r.status === "called" ? `<span class="badge called">Called ${esc(r.result.winner || "")}</span>` : `<span class="badge pre">Pre</span>`;
      const chips = [
        `<span class="chip">${r.polls.length} poll${r.polls.length === 1 ? "" : "s"}</span>`,
        r.marketProb != null ? `<span class="chip">Mkts ${ch} ${pct(r.marketProb)}</span>` : "",
        `<span class="chip">Forest ${sgn(r.forestMargin)}</span>`,
        r.marketSpread >= .15 ? `<span class="chip warn">markets split ${pct(r.marketSpread)}</span>` : "",
        r.result?.marketCall ? `<span class="chip warn">markets ≥99%</span>` : "",
      ].join("");
      return `<li class="tr ${S.sel === r.key ? "sel" : ""}" data-k="${r.key}">
        <div class="row"><span class="nm">${esc(label(r))}</span>${st}</div>
        <div class="cands">${cands(r)}</div>
        <div class="bar"><span style="width:${p * 100}%;background:${ch === "I" ? PAL.ind : PAL.dem}"></span><span style="width:${(1 - p) * 100}%;background:${PAL.rep}"></span></div>
        <div class="row"><span class="p">${leaderTxt(r)}</span><span class="hint">margin ${sgn(r.margin)} ± ${r.sigmaLive.toFixed(0)}</span>${spark(r.key)}</div>
        <div class="chips">${chips}</div></li>`;
    }).join("");
    $("tracker").querySelectorAll(".tr").forEach((el) => el.addEventListener("click", () => select(el.dataset.k)));
  }
  function renderFeeds() {
    const s = S.fc.status, li = [];
    const row = (name, ok, txt) => li.push(`<li><span>${esc(name)}</span><span class="${ok === true ? "ok" : ok === false ? "bad" : "na"}">${esc(txt)}</span></li>`);
    Object.values(s.markets || {}).forEach((m) => row(m.name, m.ok, m.ok ? `${m.races} races · ×${m.weight}` : (m.error || "offline").slice(0, 40)));
    Object.entries(s.polls || {}).forEach(([k, v]) => row(`Polls: ${k}`, !String(v).includes("fail"), v));
    Object.entries(s.macro || {}).forEach(([k, v]) => row(`FRED ${k}`, String(v).includes("ok") ? true : String(v) === "offline" ? null : false, String(v).includes("ok") ? "ok" : String(v).includes("offline") ? "bundled data" : "bundled fallback"));
    Object.entries(s.results || {}).forEach(([k, v]) => row(`Results: ${k}`, String(v).includes("fail") ? false : null, v));
    row("Early vote", null, s.earlyVote);
    $("feeds").innerHTML = li.join("");
  }

  // ------------------------------------------------------------------ model panel
  function select(key) {
    S.sel = key;
    const r = byKey(key);
    if (r && r.office !== S.office) setOffice(r.office, false);
    renderMap(); renderTracker(); renderModel(true);
    if (innerWidth <= 860) showPanel("model");
  }
  function renderModel(animate) {
    const r = byKey(S.sel), md = S.md;
    if (!r || !md) return;
    $("seedInfo").textContent = `re-grown each run · seed ${md.seed}`;
    const res = r.result;
    $("raceHead").innerHTML = `<div class="nm">${esc(label(r))}</div><div class="cands">${cands(r) || "&nbsp;"}</div>
      <div class="big"><span class="p">${leaderTxt(r)}</span><span>margin <b>${sgn(r.margin)}</b> ± ${r.sigmaLive.toFixed(1)}</span></div>
      ${res?.pctIn ? `<div class="hint">${Math.round(res.pctIn)}% of expected vote counted · counted margin ${sgn(res.margin)} · ${esc(res.source)}</div>` : ""}`;
    drawTreeVotes(r, animate); drawWaterfall(r); drawWeights(r); drawTreeGrid(r, animate); drawBacktest(); drawLive(); drawMethod();
  }

  function drawTreeVotes(r, animate) {
    const el = $("treeVotes");
    if (!r.treeMargins) { el.innerHTML = `<p class="hint">Tree-level votes are published for Senate, Governor and key House races. Forest mean: ${sgn(r.forestMargin)}.</p>`; return; }
    const W = 380, H = 170, m = { l: 18, r: 18, t: 8, b: 22 };
    const vals = r.treeMargins, lo = Math.min(-20, d3.min(vals) - 2, r.margin - 4), hi = Math.max(20, d3.max(vals) + 2, r.margin + 4);
    const x = d3.scaleLinear([lo, hi], [m.l, W - m.r]);
    const bw = (hi - lo) / 60, bins = {};
    const dots = vals.map((v, i) => { const b = Math.floor((v - lo) / bw); bins[b] = (bins[b] || 0) + 1; return { v, i, b, k: bins[b] }; });
    const maxK = d3.max(Object.values(bins)), rad = Math.max(1.4, Math.min(3, (H - m.t - m.b) / maxK / 2.1));
    el.innerHTML = "";
    const g = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`);
    g.append("g").attr("transform", `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(7).tickFormat((d) => (d > 0 ? `D+${d}` : d < 0 ? `R+${-d}` : "0"))).call((a) => a.selectAll("text").attr("fill", PAL.muted)).call((a) => a.selectAll("line,path").attr("stroke", PAL.line));
    const cx = (d) => x(lo + (d.b + 0.5) * bw), cy = (d) => H - m.b - rad - (d.k - 1) * rad * 2.05;
    const c = g.append("g").selectAll("circle").data(dots).join("circle").attr("r", rad).attr("cx", cx)
      .attr("fill", (d) => (d.v > 0 ? (party(r) === "I" ? PAL.ind : PAL.dem) : PAL.rep)).attr("opacity", .8);
    if (animate) c.attr("cy", m.t).attr("opacity", 0).transition().delay((d) => d.i * 4).duration(450).attr("cy", cy).attr("opacity", .8);
    else c.attr("cy", cy);
    const vline = (v, col, dash) => g.append("line").attr("x1", x(v)).attr("x2", x(v)).attr("y1", m.t).attr("y2", H - m.b).attr("stroke", col).attr("stroke-width", 2).attr("stroke-dasharray", dash || null);
    vline(0, PAL.muted, "4 3"); vline(r.forestMargin, PAL.ink); vline(r.margin, PAL.live);
    const share = vals.filter((v) => v > 0).length / vals.length;
    g.append("text").attr("x", W - m.r).attr("y", m.t + 10).attr("text-anchor", "end").attr("font-size", 11).attr("fill", PAL.muted)
      .text(`${Math.round(share * 100)}% of trees favour ${party(r)}`);
  }

  function drawWaterfall(r) {
    const comps = r.prior?.components || r.components || {}, w = r.componentWeights || {};
    const steps = [];
    let cur = r.anchor;
    steps.push({ l: r.polled ? "Anchor: poll average" : "Anchor: state lean + environment", a: 0, b: cur });
    steps.push({ l: "Forest correction", a: cur, b: r.forestMargin }); cur = r.forestMargin;
    if (comps.markets != null) { const v = cur + (w.markets || 0) * (comps.markets - r.forestMargin); steps.push({ l: `Markets (weight ${pct(w.markets)})`, a: cur, b: v }); cur = v; }
    if (r.earlyVoteNudge) { steps.push({ l: "Early-vote nudge", a: cur, b: cur + r.earlyVoteNudge }); cur += r.earlyVoteNudge; }
    const shift = r.status === "pre" || !S.fc.live.nResolved ? 0 : S.fc.live.nationalShift;
    if (shift && !(r.result?.pctIn >= 95)) { steps.push({ l: "National shift (decided races)", a: cur, b: cur + shift }); cur += shift; }
    if (Math.abs(r.margin - cur) > 0.05) steps.push({ l: r.result?.pctIn ? `Counted votes (${Math.round(r.result.pctIn)}% in)` : "Rounding", a: cur, b: r.margin });
    steps.push({ l: "Final margin", a: 0, b: r.margin, total: true });
    const W = 380, rowH = 22, H = steps.length * rowH + 24, ml = 160;
    const ext = d3.extent(steps.flatMap((s) => [s.a, s.b]).concat([-5, 5]));
    const x = d3.scaleLinear(ext, [ml, W - 40]).nice();
    const el = $("waterfall"); el.innerHTML = "";
    const g = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`);
    g.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", 0).attr("y2", H - 18).attr("stroke", PAL.muted).attr("stroke-dasharray", "3 3");
    steps.forEach((s, i) => {
      const y = i * rowH + 4, col = s.total ? (s.b > 0 ? (party(r) === "I" ? PAL.ind : PAL.dem) : PAL.rep) : s.b >= s.a ? PAL.dem : PAL.rep;
      g.append("text").attr("x", 0).attr("y", y + 13).attr("font-size", 11).attr("fill", s.total ? PAL.ink : PAL.muted).attr("font-weight", s.total ? 700 : 400).text(s.l);
      g.append("rect").attr("x", x(Math.min(s.a, s.b))).attr("y", y + 3).attr("height", rowH - 8).attr("rx", 2).attr("fill", col).attr("opacity", s.total ? 1 : .75)
        .attr("width", 0).transition().duration(500).delay(i * 80).attr("width", Math.max(1.5, Math.abs(x(s.b) - x(s.a))));
      g.append("text").attr("x", x(Math.max(s.a, s.b)) + 4).attr("y", y + 13).attr("font-size", 10.5).attr("fill", PAL.ink).text(s.total || i === 0 ? sgn(s.b) : sgn(s.b - s.a));
    });
    g.append("g").attr("transform", `translate(0,${H - 18})`).call(d3.axisBottom(x).ticks(5)).call((a) => a.selectAll("text").attr("fill", PAL.muted)).call((a) => a.selectAll("line,path").attr("stroke", PAL.line));
  }

  function drawWeights(r) {
    const md = S.md, L = md.featureLabels, f = r.features;
    const max = d3.max(md.weights, (w) => w.p95);
    const val = (k) => { const v = k === "anchor" ? r.anchor : f[k]; return v == null ? "—" : Math.abs(v) >= 10 ? v.toFixed(0) : v.toFixed(1); };
    $("weights").innerHTML = md.weights.map((w) => `<div class="wrow" title="Avg Spearman ρ with the polling miss: ${w.rho.toFixed(2)} · outcome ρ: ${(md.outcomeCorrelation?.[w.feature] ?? NaN).toFixed(2)} · drawn by ${pct(w.selected)} of trees · share of splits ${pct(w.importance)}">
      <span class="nm">${esc(L[w.feature] || w.feature)}</span>
      <span class="wbar"><span class="fill" style="width:${(100 * w.mean) / max}%"></span><span class="whisk" style="left:${(100 * w.p05) / max}%;width:${(100 * (w.p95 - w.p05)) / max}%"></span>${w.cycleLevel ? '<span class="cyc">cycle-level</span>' : ""}</span>
      <span class="val">${pct(w.mean, 1)} · ${val(w.feature)}</span></div>`).join("") +
      `<p class="hint">Right column: weight · this race's value. Cycle-level factors (economy, generic ballot) are judged on ${md.cycles?.length ?? 7} elections, not ${md.nRaces} races, so they only earn weight if the evidence holds up.</p>`;
  }

  function drawTreeGrid(r, animate) {
    const md = S.md, L = md.featureLabels, votes = r.sampleTreeMargins;
    const el = $("treeGrid");
    el.innerHTML = md.trees.slice(0, 24).map((t, i) => {
      const v = votes ? votes[i] : null;
      const col = v == null ? PAL.muted : v > 0 ? (party(r) === "I" ? PAL.ind : PAL.dem) : PAL.rep;
      return `<div class="tree"><div class="v" style="color:${col}">${v == null ? "·" : sgn(v)}</div><div class="f">${t.features.map((k) => esc((L[k] || k).replace("Office: ", ""))).join(" · ")}</div><div class="f">depth ${t.depth} · ${t.leaves} leaves</div></div>`;
    }).join("");
    const cards = el.querySelectorAll(".tree");
    cards.forEach((c, i) => (animate ? setTimeout(() => c.classList.add("in"), 40 * i) : c.classList.add("in")));
  }

  function drawBacktest() {
    const md = S.md;
    $("backtest").innerHTML = `<table class="bt"><tr><th>Model</th><th>RMSE</th><th>Winner</th><th>Brier</th></tr>${md.table.map((t, i) =>
      `<tr class="${i === 0 ? "me" : ""}"><td>${esc(t.model)}</td><td>${t.rmse.toFixed(2)}</td><td>${pct(t.winnerAccuracy, 1)}</td><td>${t.brier.toFixed(3)}</td></tr>`).join("")}</table>
      <p class="hint">${md.nRaces} polled races, ${md.cycles[0]}–${md.cycles.at(-1)}. Forest shrink ${md.shrink} (chosen by CV). Shared national error σ = ${md.sigmaNat.toFixed(1)} pts; race error σ: Senate ${md.sigmaRace.senate.toFixed(1)}, Governor ${md.sigmaRace.governor.toFixed(1)}, House ${md.sigmaRace.house.toFixed(1)}.</p>`;
    const W = 380, H = 150, m = 34, el = $("calib"); el.innerHTML = "";
    const x = d3.scaleLinear([0, 1], [m, W - 10]), y = d3.scaleLinear([0, 1], [H - m, 8]);
    const g = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`);
    g.append("line").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1)).attr("stroke", PAL.line).attr("stroke-dasharray", "3 3");
    g.selectAll("circle").data(md.calibration).join("circle").attr("cx", (d) => x(d.p)).attr("cy", (d) => y(d.observed)).attr("r", (d) => 2 + Math.sqrt(d.n) / 3).attr("fill", PAL.dem).attr("opacity", .75);
    g.append("g").attr("transform", `translate(0,${H - m})`).call(d3.axisBottom(x).ticks(5, "%")).call((a) => a.selectAll("text").attr("fill", PAL.muted)).call((a) => a.selectAll("line,path").attr("stroke", PAL.line));
    g.append("g").attr("transform", `translate(${m},0)`).call(d3.axisLeft(y).ticks(3, "%")).call((a) => a.selectAll("text").attr("fill", PAL.muted)).call((a) => a.selectAll("line,path").attr("stroke", PAL.line));
    g.append("text").attr("x", W - 10).attr("y", H - m - 6).attr("text-anchor", "end").attr("font-size", 10).attr("fill", PAL.muted).text("forecast P(D) → observed D win rate");
  }

  function drawLive() {
    const L = S.fc.live, e = L.ensemble;
    const bar = (c) => {
      const w = e.weights[c] ?? 0, b = e.boot?.[c];
      return `<div class="wrow"><span class="nm">${c === "forest" ? "Forest" : "Markets"}</span><span class="wbar"><span class="fill" style="width:${w * 100}%"></span>${b ? `<span class="whisk" style="left:${b.p05 * 100}%;width:${(b.p95 - b.p05) * 100}%"></span>` : ""}</span><span class="val">${pct(w, 0)} (prior ${pct(e.prior[c], 0)})</span></div>`;
    };
    $("liveLayer").innerHTML = bar("forest") + bar("markets") +
      `<p class="hint">${L.nResolved ? `${L.nResolved} races decided. Each of 500 bootstrap resamples of those races scores both sources by squared error; weights ∝ 1/MSE, shrunk toward the prior by n/(n+8). Average miss so far ${sgn(L.rawMiss)} pts → shift ${sgn(L.nationalShift)} applied to uncounted races; national σ now ${L.sigmaNat}.` :
        "No races decided yet, so the forest/market blend sits at its prior. When polls close it starts learning from called races."}</p>`;
  }

  function drawMethod() {
    if ($("method").dataset.done) return;
    const md = S.md;
    $("method").dataset.done = 1;
    $("method").innerHTML = `
      <h4>1 · Training data</h4><p>${md.nRaces} Senate, Governor and House general elections from ${md.cycles[0]} to ${md.cycles.at(-1)} that had polls in their final 21 days, with the certified result (FiveThirtyEight polling archive). Each race also gets: the state's lean in the previous presidential election (county returns, 2008–2024); the national generic-ballot average; whether it was a midterm and who held the White House; and CPI inflation, WTI oil and unemployment changes over the year before the election. Economic factors are signed so that positive values help the party out of power.</p>
      <h4>2 · Correlation-weighted bootstrap forest</h4><p>Each of ${md.nTrees} trees draws a bootstrap sample of races. On that sample it measures how strongly each factor correlates with the result (Spearman ρ, discounted for missing data and, for factors that only change once per election, judged on the number of elections). It then draws a subset of factors with probability ∝ those correlations and grows a regression tree. Because the weights are re-estimated on every resample, you see their full distribution, not a single number. The forest learns corrections to an anchor (the poll average, or state lean plus environment when no polls exist), and how much of the correction to trust (${md.shrink}) is chosen by leave-one-cycle-out cross-validation.</p>
      <h4>3 · Uncertainty</h4><p>Errors are split into a shared national term (σ ${md.sigmaNat.toFixed(1)}: polls in 2016 and 2020 missed in the same direction everywhere) and a race-specific term. Win probability = Φ(margin / σ). Seat totals come from 20,000 correlated simulations.</p>
      <h4>4 · Live ensemble</h4><p>The forest is blended with the prediction-market consensus (Kalshi, Polymarket, PredictIt, Manifold). Market probability is converted to a margin using the same σ. The blend starts at a prior (${pct(S.fc.live.ensemble.prior.forest)} forest). On election night it is re-estimated by bootstrapping over decided races. Early-vote party swing, where supplied, adds a small nudge.</p>
      <h4>5 · Counting</h4><p>Counted votes are folded in as margin = r·counted + (1−r)·forecast, with r = (share of expected vote in)^1.5, so early, unrepresentative batches move the estimate less. AP calls, or manual calls, override the model. Markets at ≥99¢ are flagged.</p>
      <h4>Limits</h4><ul><li>Economic factors vary only by election (7 data points), so they rarely earn much weight. That is the honest answer, not a bug.</li><li>Unpolled races rely on state lean (σ ≈ ${md.sigmaNoPoll.toFixed(0)} pts). House districts use state lean unless a district-lean file is supplied.</li><li>Prediction-market and early-vote history isn't in the training data, which is why they enter at layer 2 rather than in the trees.</li></ul>`;
  }

  // ------------------------------------------------------------------ controls
  function setOffice(o, rerender = true) {
    S.office = o; S.selState = null;
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.o === o));
    if (rerender) renderMap();
  }
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setOffice(b.dataset.o)));
  function showPanel(p) {
    document.querySelectorAll(".mtabs button").forEach((b) => b.classList.toggle("on", b.dataset.p === p));
    ["model", "map", "tracker"].forEach((x) => $(`p-${x}`).classList.toggle("hide-m", x !== p));
    scrollTo({ top: $("mtabs").offsetTop - 4, behavior: "smooth" });
  }
  document.querySelectorAll(".mtabs button").forEach((b) => b.addEventListener("click", () => showPanel(b.dataset.p)));
  if (innerWidth <= 860) ["model", "tracker"].forEach((x) => $(`p-${x}`).classList.add("hide-m"));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { palette(); if (S.fc) renderAll(false); });

  palette();
  fetch("vendor/states-10m.json").then((r) => r.json()).then((us) => {
    window.__US = us;
    load(true);
    setInterval(() => load(false), REFRESH);
    setInterval(() => load(true), REFRESH * 5);
  });
})();
