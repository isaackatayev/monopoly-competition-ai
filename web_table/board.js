/* The board can render before a table is joined. Native deed values replace the
   initial print layout when a game snapshot arrives. */
(() => {
  "use strict";

  const colors = ["#9d6b55", "#91bfce", "#d597b3", "#e7a153", "#db655e", "#e3c757", "#75a580", "#6885b4"];
  const groupNames = ["Brown", "Light blue", "Pink", "Orange", "Red", "Yellow", "Green", "Dark blue", "Railroad", "Utility"];
  const layout = [
    ["GO"], ["Mediterranean Avenue",0,60], ["Community Chest"], ["Baltic Avenue",0,60], ["Income Tax"], ["Reading Railroad",8,200], ["Oriental Avenue",1,100], ["Chance"], ["Vermont Avenue",1,100], ["Connecticut Avenue",1,120],
    ["Jail / Just Visiting"], ["St. Charles Place",2,140], ["Electric Company",9,150], ["States Avenue",2,140], ["Virginia Avenue",2,160], ["Pennsylvania Railroad",8,200], ["St. James Place",3,180], ["Community Chest"], ["Tennessee Avenue",3,180], ["New York Avenue",3,200],
    ["Free Parking"], ["Kentucky Avenue",4,220], ["Chance"], ["Indiana Avenue",4,220], ["Illinois Avenue",4,240], ["B. & O. Railroad",8,200], ["Atlantic Avenue",5,260], ["Ventnor Avenue",5,260], ["Water Works",9,150], ["Marvin Gardens",5,280],
    ["Go To Jail"], ["Pacific Avenue",6,300], ["North Carolina Avenue",6,300], ["Community Chest"], ["Pennsylvania Avenue",6,320], ["Short Line",8,200], ["Chance"], ["Park Place",7,350], ["Luxury Tax"], ["Boardwalk",7,400]
  ];
  const special = {
    0:["Collect $200 when you pass GO.", "go"], 2:["Draw a Community Chest card.", "chest"],
    4:["Pay $200 to the bank.", "coins"], 7:["Draw a Chance card.", "chance"],
    10:["In jail or just visiting.", "jail"], 17:["Draw a Community Chest card.", "chest"],
    20:["Rest here. No payment is due.", "car"], 22:["Draw a Chance card.", "chance"],
    30:["Move directly to jail without collecting $200.", "whistle"],
    33:["Draw a Community Chest card.", "chest"], 36:["Draw a Chance card.", "chance"],
    38:["Pay $100 to the bank.", "diamond"]
  };
  const icons = {
    die:'<rect x="4" y="4" width="16" height="16" rx="3"/><g fill="currentColor" stroke="none"><circle cx="8" cy="8" r="1.55"/><circle cx="16" cy="8" r="1.55"/><circle cx="12" cy="12" r="1.55"/><circle cx="8" cy="16" r="1.55"/><circle cx="16" cy="16" r="1.55"/></g>',
    dice:'<g transform="rotate(-12 9 14)"><rect x="2" y="8" width="13" height="13" rx="2.5"/><circle cx="6" cy="12" r=".7"/><circle cx="11" cy="17" r=".7"/></g><path d="m9 5 8-3 5 12-5 2"/><circle cx="15" cy="7" r=".7"/>',
    joystick:'<rect x="2.5" y="2.5" width="19" height="19" rx="4"/><g fill="currentColor" stroke="none"><circle cx="7.5" cy="7.5" r="1.65"/><circle cx="16.5" cy="7.5" r="1.65"/><circle cx="12" cy="12" r="1.65"/><circle cx="7.5" cy="16.5" r="1.65"/><circle cx="16.5" cy="16.5" r="1.65"/></g>',
    invite:'<circle cx="9" cy="8" r="3.2"/><path d="M2.5 19c.5-4 3-6 6.5-6 2.4 0 4.2.8 5.3 2.1M18.5 7v7m-3.5-3.5h7"/>',
    people:'<circle cx="9" cy="8" r="3"/><path d="M2.5 19c.5-4 3-6 6.5-6 3.4 0 5.9 2 6.5 6M15 5.4a3 3 0 0 1 0 5.6m2.6 2.6c2.1.7 3.5 2.4 3.9 5.4"/>',
    activity:'<path d="M3 6h13M3 12h17M3 18h9"/>', cycle:'<path d="M20 10a8 8 0 0 0-13.6-4L3.5 9m0-5v5h5m-5 5a8 8 0 0 0 13.6 4l3-3m0 5v-5h-5"/>',
    plus:'<path d="M12 5v14M5 12h14"/>', close:'<path d="m6 6 12 12M18 6 6 18"/>',
    book:'<path d="M12 6c-2.8-2.1-6-2.4-9-1.1v13.3c3.1-1.3 6.3-1 9 1.1 2.8-2.1 6-2.4 9-1.1V4.9c-3-1.3-6.2-1-9 1.1Zm0 0v13.3"/>',
    pointer:'<path d="m5 3 13 9-6 1-2.2 6z"/>', network:'<rect x="8.5" y="2.5" width="7" height="5" rx="1"/><rect x="2" y="16" width="7" height="5" rx="1"/><rect x="15" y="16" width="7" height="5" rx="1"/><path d="M12 8v4m-7 4v-4h14v4"/>',
    train:'<path fill="currentColor" d="M4 5h5l-.6 4h4.5V5h-1V3h8v2h-1v9H4c-2 .2-2-5 1-5z"/><path d="M3 15h17l2 3h-3M1 21h22"/><g fill="var(--paper,#f5edd9)"><circle cx="6" cy="16" r="2"/><circle cx="12" cy="16" r="2"/><circle cx="18" cy="16" r="2"/></g>',
    chest:'<path fill="#97b8bb" d="M2.5 10h19v10h-19z"/><path fill="#bfd0b8" d="M2.5 10q.2-6.8 5-6.8h9q4.8 0 5 6.8z"/><path d="M6.5 4.1 5.7 10v10m11.5-16 .9 6v10m-15.4-7.5h18.8"/><path fill="#e9b84e" d="M10 10h4v5h-4z"/>',
    coins:'<path fill="#d4ad63" d="M3 8v4c0 1.5 3.7 2.5 7 2.5s7-1 7-2.5V8"/><ellipse cx="10" cy="8" rx="7" ry="2.5" fill="#e9cc8a"/><path fill="#d4ad63" d="M7 15v3.5c0 1.5 3.7 2.5 7 2.5s7-1 7-2.5V15"/><ellipse cx="14" cy="15" rx="7" ry="2.5" fill="#e9cc8a"/>',
    bulb:'<path fill="#ebc975" d="M9 17c0-3-4-4-4-8a7 7 0 1 1 14 0c0 4-4 5-4 8z"/><path d="M9 18h6m-5 3h4m-2-5V9m-2 1 2-2 2 2"/>',
    water:'<path fill="#a3bcb6" d="M3 9h6V7h7v2h1c3 0 5 2 5 5v3h-5v-3h-5l-2 1H3z"/><path d="M12 7V4M8 3h8M2 8v8"/><path fill="#5f98a5" d="M19 18c0 .9-2 2.3-2 3.4 0 2.3 4 2.3 4 0 0-1.1-2-2.5-2-3.4z"/>',
    diamond:'<path fill="#afd1d0" d="m2 8 4-5h12l4 5-10 13z"/><path d="M2 8h20M7 8l5 13 5-13-5-5z"/>',
    hub:'<path d="m3 12 9-8 9 8m-15-2v11h12V10"/><path d="M10 21v-6h4v6"/>',
    robot:'<rect x="3" y="7" width="18" height="14" rx="4"/><path d="M12 7V3m-2 13h4"/><circle cx="8.5" cy="12" r=".9"/><circle cx="15.5" cy="12" r=".9"/>',
    person:'<circle cx="12" cy="8" r="3.7"/><path d="M4.5 21c.4-5 3.3-7.5 7.5-7.5s7.1 2.5 7.5 7.5"/>',
    hat:'<path fill="currentColor" d="m6 5 12-1-.8 12c2.5-.1 5 .3 5.2 1.9.5 3-20 5-20.6 1.9-.3-1.5 2.6-2.4 5.9-3z"/><path stroke="var(--paper,#f3ead6)" d="m8 14 8-1"/>',
    ship:'<path fill="currentColor" d="M2 15h20l-4.2 5H6zM6 11h10l2 4H5zM8 6h3v5H8zm5-2h3v7h-3z"/><path d="M3 22q2-1.5 4 0 2-1.5 4 0 2-1.5 4 0 2-1.5 4 0"/>',
    car:'<path fill="#dc6550" d="m3 12 2-1 3.1-6 8.1-.3 3.6 5 2.3 1.8v5H2z"/><path fill="#c4d1c0" d="m6.4 10.6 2.7-4.4h3l.2 4.4zm7-4.4h2.4l3 4.1h-5.3z"/><path d="M3 14h2m13-1h3M2 17h20"/><g fill="#253d3d"><circle cx="6.8" cy="17.4" r="2.3"/><circle cx="17.8" cy="17.4" r="2.3"/></g>',
    jail:'<path fill="#f1b66a" d="M1.5 2h21v20h-21z"/><path fill="#f2dec2" d="M5 5h14v14H5z"/><path fill="#313d3a" d="M7 19c0-5 9-5 10 0z"/><circle cx="12" cy="11.5" r="3" fill="#c5a078"/><path d="M8 5v14m4-14v14m4-14v14"/>',
    whistle:'<path fill="#7197a6" d="m5 7 15-2 1 5-5.7 1.6c0 6-4.4 8-8 6.6C1 15.7 2.5 9.5 5 7z"/><circle cx="8.7" cy="13" r="2.5" fill="#b3c8c2"/><path d="m13 6 1 3 3-.5-.8-3M3 4 1 2m7 1V1"/>',
    go:'<path d="M21 12H3m0 0 6 5m-6-5 6-5" stroke="#d85943" stroke-width="4"/>'
  };
  function icon(name, size = 20) {
    const content = icons[name] || icons.die;
    return '<svg class="ui-icon" aria-hidden="true" width="'+size+'" height="'+size+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'+content+'</svg>';
  }
  function token(seat) { return icon(["hat", "ship", "car", "hub"][seat] || "person"); }
  document.querySelectorAll("[data-icon]").forEach(el => { el.innerHTML = icon(el.dataset.icon); });

  const board = document.getElementById("board");
  const dialog = document.getElementById("property-dialog");
  const cells = [];
  let selected = null;
  let data = {players:[],owners:[],levels:[],mortgaged:[],board:[]};

  function tileArt(index, group) {
    if (group === 8) return icon("train", 26);
    if (group === 9) return icon(index === 12 ? "bulb" : "water", 26);
    const type = special[index]?.[1];
    if (type === "chance") return '<b class="question-mark">?</b>';
    return type ? icon(type, 26) : "";
  }
  function buildTile(index) {
    const [name, group = -1, price = 0] = layout[index];
    const tile = document.createElement("button");
    const corner = index % 10 === 0;
    const side = index < 10 ? "bottom" : index < 20 ? "left" : index < 30 ? "top" : "right";
    tile.type = "button";
    tile.className = "tile "+(corner ? "corner corner-"+index : "side-"+side)+(group >= 0 && group < 8 ? " street" : " special");
    tile.style.gridRow = index <= 10 ? 11 : index < 20 ? 21-index : index <= 30 ? 1 : index-29;
    tile.style.gridColumn = index <= 10 ? 11-index : index < 20 ? 1 : index <= 30 ? index-19 : 11;
    tile.title = name+(price ? " · $"+price : "");
    tile.setAttribute("aria-label", tile.title);
    const label = index === 10 ? "Just Visiting" : name;
    const priceLabel = price ? "$"+price : index === 4 ? "$200" : index === 38 ? "$100" : "";
    const art = tileArt(index, group);
    const body = '<span class="cell-name">'+label+'</span>'+(art ? '<span class="cell-art">'+art+'</span>' : '')+(priceLabel ? '<span class="cell-price">'+priceLabel+'</span>' : '');
    const band = group >= 0 && group < 8 ? '<span class="cell-band" style="background:'+colors[group]+'"></span>' : '';
    tile.innerHTML = '<span class="cell-inner">'+band+'<span class="cell-body">'+body+'</span></span><span class="deed-marker"></span><span class="pawns"></span>';
    tile.addEventListener("click", () => { selected = index; renderDetails(); dialog.showModal(); });
    tile.addEventListener("keydown", ev => {
      if (!["ArrowLeft", "ArrowRight"].includes(ev.key)) return;
      ev.preventDefault();
      cells[(index + (ev.key === "ArrowLeft" ? -1 : 1) + 40) % 40].focus();
    });
    board.appendChild(tile);
    cells[index] = tile;
  }
  layout.forEach((_,index) => buildTile(index));

  const valueRow = (key, value) => {
    const row = document.createElement("div");
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = key;
    dd.textContent = value;
    row.append(dt, dd);
    return row;
  };
  const money = value => "$"+Number(value).toLocaleString("en-US");
  function renderDetails() {
    if (selected === null) return;
    const index = selected;
    const [name,group = -1,price = 0] = layout[index];
    const deed = data.board?.find(item => item.square === index);
    document.getElementById("property-name").textContent = deed?.name || name;
    document.getElementById("property-type").textContent = group >= 0 ? "Title deed · "+groupNames[group] : "Board space";
    document.getElementById("property-band").style.background = colors[group] || "#b1c6a8";
    const details = document.getElementById("property-details");
    details.replaceChildren();
    if (special[index]) {
      const p = document.createElement("p");
      p.textContent = special[index][0];
      details.appendChild(p);
    }
    const stats = document.createElement("dl");
    if (price) {
      const owner = data.owners?.[index];
      const player = data.players.find(p => p.seat === owner);
      stats.append(valueRow("Price", money(deed?.price || price)), valueRow("Owner", owner == null || owner < 0 ? "Bank" : player?.name || "Seat "+(owner+1)));
      if (data.mortgaged?.[index]) stats.append(valueRow("Status", "Mortgaged"));
      if (data.levels?.[index] > 0) stats.append(valueRow("Buildings", data.levels[index] === 5 ? "One hotel" : data.levels[index]+" houses"));
      if (deed) {
        stats.append(valueRow("Mortgage value", money(deed.mortgage)));
        if (group < 8 && deed.rents?.length) {
          stats.append(valueRow("Base rent", money(deed.rents[0])), valueRow("House price", money(deed.building_cost)));
          deed.rents.slice(1).forEach((rent,i) => stats.append(valueRow(i === 4 ? "Rent with hotel" : "Rent with "+(i+1)+" "+(i ? "houses" : "house"), money(rent))));
        } else if (group === 8) stats.append(valueRow("Rent by railroads owned", "$25 / $50 / $100 / $200"));
        else if (group === 9) stats.append(valueRow("Rent by utilities owned", "4× / 10× a fresh roll"));
      }
    }
    const here = data.players.filter(p => p.position === index).map(p => p.name || "Seat "+(p.seat+1));
    if (here.length) stats.append(valueRow("On this space", here.join(", ")));
    if (stats.children.length) details.appendChild(stats);
  }
  document.getElementById("property-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", e => { if (e.target === dialog) {
    const r = dialog.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close();
  }});
  function render(snapshot = {}) {
    const previous = data.players;
    data = {players:[], owners:[], levels:[], mortgaged:[], board:[], ...snapshot};
    const dice = data.dice;
    document.getElementById("dice-result").textContent = Array.isArray(dice) && dice.length === 2 ? dice.join(" + ") : "—";
    cells.forEach((tile, index) => {
      const marker = tile.querySelector(".deed-marker");
      const owner = data.owners?.[index];
      const level = data.levels?.[index] || 0;
      marker.className = "deed-marker";
      marker.textContent = "";
      if (owner != null && owner >= 0) {
        marker.classList.add("claimed", "seat-"+owner);
        marker.textContent = data.mortgaged?.[index] ? "M" : level === 5 ? "H" : level || "";
      }
      const pawns = tile.querySelector(".pawns");
      const occupants = data.players.filter(p => p.position === index);
      const ownerName = data.players.find(p => p.seat === owner)?.name;
      const spoken = [tile.title];
      if (ownerName) spoken.push("Owned by "+ownerName);
      for (const p of occupants) spoken.push((p.name || "Seat "+(p.seat+1))+" is here");
      tile.setAttribute("aria-label", spoken.join(". "));
      const occupantKey = JSON.stringify(occupants.map(p => [p.seat, p.name]));
      if (pawns.dataset.occupants === occupantKey) return;
      pawns.dataset.occupants = occupantKey;
      pawns.replaceChildren();
      occupants.forEach(p => {
        const node = document.createElement("span");
        node.className = "pawn seat-"+p.seat;
        if (previous.some(prior => prior.seat === p.seat && prior.position !== index)) node.classList.add("arriving");
        node.title = p.name || "Seat "+(p.seat+1);
        node.innerHTML = token(p.seat);
        pawns.append(node);
      });
    });
    if (dialog.open) renderDetails();
  }
  window.MonopolyBoard = {render, icon, token, name:index => layout[index]?.[0] || "Unknown space"};
  // Draw the city's architecture in one two-color print perspective. Each face
  // has its own window grid, so the drawing scales with the physical board.
  function building(x, y, w, d, h, {front="#9bb6aa", side="#54837c", cols=5, rows=4, roof="#d3d5b8", windows="#365955"} = {}) {
    const slope=.39, depth=.55;
    const left=x-w, backY=y-w*slope, right=x+d, sideY=y-d*depth;
    const frontFace = left+','+backY+' '+x+','+y+' '+x+','+(y-h)+' '+left+','+(backY-h);
    const sideFace = x+','+y+' '+right+','+sideY+' '+right+','+(sideY-h)+' '+x+','+(y-h);
    const lid = left+','+(backY-h)+' '+(left+d)+','+(backY-h-d*depth)+' '+right+','+(sideY-h)+' '+x+','+(y-h);
    let s='<g stroke="#304e49" stroke-linejoin="round" stroke-width="1.35"><polygon fill="'+front+'" points="'+frontFace+'"/><polygon fill="'+side+'" points="'+sideFace+'"/><polygon fill="'+roof+'" points="'+lid+'"/>';
    const faceGrid=(span, count, dir) => {
      let windowsSvg="";
      const step=(span-6)/Math.max(1,count), rh=(h-7)/Math.max(1,rows);
      for(let c=0;c<count;c++) for(let r=0;r<rows;r++) {
        const offset=3+(c+.23)*step, ww=step*.52;
        const X=x+dir*offset, Y=y-h+5+(r+.12)*rh-offset*(dir === -1 ? slope : depth);
        const delta=-ww*(dir === -1 ? slope : depth);
        windowsSvg+='<path stroke="none" fill="'+windows+'" d="M'+X+' '+Y+'l'+(dir*ww)+' '+delta+'v'+(rh*.51)+'l'+(-dir*ww)+' '+(-delta)+'z"/>';
      }
      return windowsSvg;
    };
    s+=faceGrid(w,cols,-1)+faceGrid(d,Math.max(1,Math.round(cols*d/w)),1);
    s+='<path fill="none" d="m'+left+' '+(backY-h+3.7)+' '+w+' '+(w*slope)+' '+d+' '+(-d*depth)+'"/></g>';
    return s;
  }
  const outline = "#304e49";
  function pointTree(x,y,scale=1) {
    return '<g transform="translate('+x+' '+y+') scale('+scale+')" stroke="'+outline+'" stroke-linejoin="round"><path d="m-9-3 19 1-10 3z" fill="#92a899" stroke="none"/><path d="M0 0v-17" stroke-width="2.5"/><path fill="#679283" stroke-width="1.3" d="M-2-9c-9 1-11-6-7-9-3-4-1-9 2-10-.1-7 8-9 10-4 5-.8 8 4 6 8 5 3 4 10-1 12-1 4-6 5-10 3z"/><path fill="none" d="M0-8v-16m0 9-4-4m4 1 4-5"/></g>';
  }
  function cityArt() {
    let s='<svg viewBox="0 0 440 220" preserveAspectRatio="xMidYMax meet" aria-hidden="true"><defs><pattern id="sun-dots" width="3.5" height="3.5" patternUnits="userSpaceOnUse"><circle cx="1.6" cy="1.6" r=".65" fill="#bd6450"/></pattern><pattern id="ground-lines" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(21)"><path d="M0 0v5" stroke="#839c89" stroke-width=".8"/></pattern></defs>';
    s+='<circle cx="270" cy="63" r="37" fill="#e6b99a"/><circle cx="270" cy="63" r="37" fill="url(#sun-dots)"/><g fill="none" stroke="#7f9b89" stroke-linecap="round" stroke-width="1.25"><path d="M81 66h18c4-7 12-7 16 0h10c2-4 6-5 10-4M298 38h13c5-8 12-8 16-1h19m-265 43h24m237-10h14m-213-41h16"/><path d="m81 85 3-3 4 2 3-3m237-30 3-2 3 1 2-2"/></g>';
    s+='<path d="m30 152 151-77 225 67-139 70z" fill="url(#ground-lines)"/><g fill="#b9c5aa" stroke="'+outline+'" stroke-width="1.35" stroke-linejoin="round"><path d="m53 139 124-62 213 68-123 63z"/><path d="m53 139 214 69 123-63v4l-123 63-214-69z" fill="#87a493"/></g>';
    s+=building(145,129,29,15,43,{cols:3,rows:3,front:"#aec3b1"});
    s+=building(172,124,25,17,59,{cols:3,rows:5,front:"#a9b7a3"});
    s+=building(171,67,17,11,7,{rows:0,front:"#6b9285"});
    s+=building(214,143,47,28,83,{cols:5,rows:6,front:"#a7bca9",side:"#6e9589"});
    s+=building(212,60,34,22,12,{cols:4,rows:1,front:"#c9c9aa"});
    s+=building(211,47,22,12,10,{cols:3,rows:1,front:"#b9c4ab"});
    s+=building(210,38,12,7,10,{cols:2,rows:1,front:"#92a999"});
    s+='<path d="M206 25V13m0 3 7 2-7 2" fill="#c46a52" stroke="'+outline+'" stroke-width="1.2"/>';
    s+=building(247,149,25,15,40,{cols:3,rows:3,front:"#c3cbb1"});
    s+=building(284,151,34,19,64,{cols:4,rows:5,front:"#96b2a2",side:"#4e7e74"});
    s+=building(282,88,25,13,6,{cols:4,rows:0,front:"#7a9c8b"});
    s+='<g stroke="'+outline+'" stroke-width="1.5" stroke-linejoin="round"><path d="M266 76v-7l12 3v7m-6-7v6" fill="none"/><path d="m263 58 6-7 15 3 4 8-12 5z" fill="#617f70"/><path d="m264 58 12 4.5 11-2v13l-11 3-12-4z" fill="#d5b08b"/><path d="M276 63v13m-8-16v13m16-11v11" fill="none"/></g>';
    s+=building(306,157,18,11,34,{cols:2,rows:3,front:"#ced1b5"});
    s+=pointTree(349,146,.68)+pointTree(363,150,.48);
    // A narrow station building along the near street.
    s+=building(130,155,47,20,27,{cols:5,rows:2,front:"#d5c29d",side:"#829d88"});
    s+='<g stroke="'+outline+'" stroke-width="1.3" stroke-linejoin="round"><path d="m82 110 10-8 47 18 11 10-20 0z" fill="#b77058"/><path d="m92 102 19-9 46 17-18 10z" fill="#d39574"/><path d="m139 120 18-10-7 20-20 0z" fill="#c78867"/><path d="m106 100 40 15m-25-13 32 11" fill="none" stroke="#956b56"/></g>';
    s+=pointTree(72,141,.7)+pointTree(79,148,.52);
    s+=building(180,168,31,22,26,{cols:4,rows:2,front:"#c6c6a8",side:"#799786"});
    s+=building(176,143,20,13,6,{cols:2,rows:0,front:"#bfbb97"});
    s+=building(226,180,32,24,46,{cols:4,rows:4,front:"#bbba96",side:"#738f7d"});
    s+=building(222,134,20,13,7,{cols:2,rows:0,front:"#d4c2a0"});
    // Sloped roof and a chimney distinguish the near block of houses.
    s+=building(277,186,44,22,24,{cols:5,rows:2,front:"#d1c4a3",side:"#91a18a"});
    s+='<g stroke="'+outline+'" stroke-width="1.35" stroke-linejoin="round"><path d="m231 145 10-10 45 17 13 11-22 1z" fill="#b76c54"/><path d="m241 135 22-9 45 16-22 10z" fill="#d18c6b"/><path d="m286 152 22-10-9 21-22 1z" fill="#a76952"/><path d="m255 136 40 14m-30-15 36 12" fill="none" stroke="#a17053"/><path d="M263 133v-9l6 2v9z" fill="#ac815e"/></g>';
    s+=pointTree(330,168,.62)+pointTree(317,176,.57)+pointTree(299,187,.5);
    s+='<g stroke="'+outline+'" stroke-width="1.45" stroke-linejoin="round"><path d="m110 161 6-2 12 4 2 4-6 3-14-5z" fill="#da9f59"/><path d="m114 161 3-3 7 2 2 4z" fill="#d3c9a3"/><path d="m185 188 6-2 11 4 2 4-6 2-13-5z" fill="#ce7861"/><path d="m189 188 2-3 7 2 2 4z" fill="#d3c9a3"/><path d="m98 155-8-3m71 23-13-5m94 34-12-4m113-29 11-5" stroke="#d5cfb0"/></g>';
    return s+'</svg>';
  }
  document.getElementById("center-art").innerHTML = cityArt();
})();
