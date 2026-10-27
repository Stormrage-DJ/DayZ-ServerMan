"""Check R03 transforms, strict precision and display-only navigation in composed Edge."""

from __future__ import annotations

import unittest

from tests.ui_harness_support import EDGE, run_editor_harness


# Independent numerical comparisons allow floating arithmetic, never precision-threshold tolerance.
COMMON = r'''
await window.ServerManMapEditor.load(); await wait();
const coordinates = window.ServerManMapCoordinates;
const makeBounds = (x_min, z_min, width, height) => ({x_min, z_min, width, height});
const close = (actual, expected, text) => check(Math.abs(actual-expected) <= 1e-8, text);
const region = document.getElementById("map-viewport");
const view = window.ServerManMapViewportUi.view();
const loadBounds = async (bounds) => {
  editorAnswer = {success: true, value: {...editorValue, plan: {terrain: {terrain_id: "synthetic", bounds}}}};
  await window.ServerManMapEditor.load(); await wait();
};
'''


@unittest.skipUnless(EDGE.is_file(), "Microsoft Edge is unavailable")
class MissionMapEditorViewportEdgeTests(unittest.TestCase):
    """Exercise only synthetic plans and isolated headless browser profiles."""

    def test_r03_round_trips_and_native_metre_distances(self) -> None:
        """Nonzero non-square extents and many points preserve native authority."""
        self.assertEqual(run_editor_harness(COMMON + r'''
for (const bounds of [makeBounds(0,0,20480,20480), makeBounds(-715.125,291.75,8000,3100)]) {
  for (const [h,v] of [[0,0],[5,5],[10,10],[2.23456789,8.7654321]]) {
    const native = coordinates.fromGrid({h,v},bounds);
    close(native.x,bounds.x_min+bounds.width*h/10,"native X oracle");
    close(native.z,bounds.z_min+bounds.height*v/10,"native Z oracle");
    const grid = coordinates.grid(native,bounds);
    close(grid.h,h,"grid X round trip"); close(grid.v,v,"grid Z round trip");
    const normalized = coordinates.normalized(native,bounds);
    close(normalized.u,h/10,"normalized X"); close(normalized.v,v/10,"normalized Z");
    const back = coordinates.native(normalized,bounds);
    close(back.x,native.x,"native round trip"); close(back.z,native.z,"native round trip Z");
  }
  for(let index=0;index<400;index++) {
    const point={x:bounds.x_min+bounds.width*((index*73)%401)/401,
      z:bounds.z_min+bounds.height*((index*97)%401)/401};
    const again=coordinates.fromGrid(coordinates.grid(point,bounds),bounds);
    close(again.x,point.x,"many native X"); close(again.z,point.z,"many native Z");
  }
}
const centre=coordinates.fromGrid({h:5,v:5},makeBounds(0,0,20480,20480));
check(centre.x===10240 && centre.z===10240,"accepted centre example");
check(coordinates.distance({x:0,z:0},{x:3,z:4})===5 && coordinates.metres(5)==="5 m","metre distance");
for(const invalid of [null,{},makeBounds(0,0,0,3),makeBounds(0,0,1,-3),makeBounds(NaN,0,1,3),
  makeBounds(0,0,"1",3)]) check(coordinates.bounds(invalid)===null,"unknown bounds are not guessed");
'''), "PASS")

    def test_strict_decade_precision_clamps_axes_and_monotonicity(self) -> None:
        """Exact equality fails directly; Grid uses the larger transformed displacement."""
        self.assertEqual(run_editor_harness(COMMON + r'''
const steps=[1,.1,.01,.001,.0001,.00001,.000001];
for(const [minimum,maximum] of [[0,3],[1,6]]) {
  check(coordinates.precision(100,minimum,maximum)===minimum,"minimum clamp exception");
  check(coordinates.precision(1e-12,minimum,maximum)===maximum,"maximum clamp");
  for(let decimals=minimum;decimals<=maximum;decimals++) {
    const step=steps[decimals];
    const previous=Math.max(minimum,decimals-1);
    check(coordinates.precision(step,minimum,maximum)===previous,`strict equality ${decimals}`);
    check(coordinates.precision(step*(1-Number.EPSILON),minimum,maximum)===decimals,`below decade ${decimals}`);
    check(coordinates.precision(step*(1+Number.EPSILON),minimum,maximum)===previous,`above decade ${decimals}`);
  }
}
const bounds=makeBounds(-200.25,70.125,1000,10000),point={x:50.123456789,z:888.7654321};
check(coordinates.display(point,bounds,10,"Grid")==="Grid X 2.50, Z 0.82","larger X displacement and strict .001 equality");
const reversed=makeBounds(70.125,-200.25,10000,1000);
check(coordinates.display({x:point.z,z:point.x},reversed,10,"Grid")==="Grid X 0.82, Z 2.50","larger Z displacement");
let nativePrevious=0,gridPrevious=1;
for(let scale=.001;scale<100000;scale*=1.2) {
  const native=coordinates.precision(1/scale,0,3),grid=coordinates.precision(Math.max(10/(scale*1000),10/(scale*10000)),1,6);
  check(native>=nativePrevious&&grid>=gridPrevious,"zoom precision monotonic");
  nativePrevious=native;gridPrevious=grid;
}
const snapshot=JSON.stringify(point);coordinates.display(point,bounds,20,"Both");
check(JSON.stringify(point)===snapshot,"display rounds no stored point");
'''), "PASS")

    def test_zoom_pan_fit_resize_and_frozen_plan_have_no_drift(self) -> None:
        """Navigation keeps cursor anchors and immutable stored geometry at all three sizes."""
        for size in ("960,600", "1280,800", "1920,1080"):
            with self.subTest(size=size):
                self.assertEqual(run_editor_harness(COMMON + r'''
const bounds=Object.freeze(makeBounds(-12345.75,456.125,20480,8192));
const geometry=Object.freeze({x:-913.123456789,z:1845.987654321,radius:38.123456789});
const plan=Object.freeze({terrain:Object.freeze({terrain_id:"synthetic",bounds}),objects:Object.freeze([geometry])});
editorAnswer={success:true,value:{...editorValue,plan}};await window.ServerManMapEditor.load();await wait();
const saved=JSON.stringify(plan),anchor={x:region.clientWidth*.41,y:region.clientHeight*.63};
const native=view.toNative(anchor),initial=view.snapshot();
for(let repeat=0;repeat<150;repeat++) {
  view.zoom(anchor,-10);view.pan(4.125,-7.375);view.pan(-4.125,7.375);view.zoom(anchor,10);
}
const recovered=view.toNative(anchor);close(recovered.x,native.x,"cursor X invariance");close(recovered.z,native.z,"cursor Z invariance");
check(JSON.stringify(plan)===saved,"loaded geometry untouched");
view.zoom(anchor,-1e8);check(view.snapshot().scale===20,"maximum CSS px per metre");
view.zoom(anchor,1e8);check(view.snapshot().scale===view.snapshot().fitScale/2,"half fit minimum");
document.getElementById("map-fit").click();const fitted=view.snapshot();
close(fitted.scale,Math.min(region.clientWidth/bounds.width,region.clientHeight/bounds.height),"real fit scale");
const bottom=view.toScreen({x:bounds.x_min,z:bounds.z_min});const top=view.toScreen({x:bounds.x_min,z:bounds.z_min+bounds.height});
check(top.y<bottom.y,"native Z rises while screen Y falls");
const centre=view.toNative({x:fitted.width/2,y:fitted.height/2});
document.getElementById("map-panel-toggle").click();await wait();
const resized=view.snapshot();check(resized.width>fitted.width,"ResizeObserver follows panel collapse");
const after=view.toNative({x:resized.width/2,y:resized.height/2});close(after.x,centre.x,"resize centre X");close(after.z,centre.z,"resize centre Z");
check(document.querySelectorAll("#map-vector line").length===22,"complete terrain grid");
check(document.querySelector("#map-vector text").textContent.includes("X"),"screen edge axis labels");
for (const mode of ["Grid", "Native", "Both"]) {
  for (const width of [580, 900]) {
    view.resize(width, 600); view.fit();
    window.ServerManMapViewportRender.draw(view, mode);
    const labels=[...document.querySelectorAll(".map-grid-labels text")].map(label=>label.getBoundingClientRect());
    for (let i=0;i<labels.length;i++) for (let j=i+1;j<labels.length;j++) {
      const a=labels[i],b=labels[j];
      check(a.right<=b.left||b.right<=a.left||a.bottom<=b.top||b.bottom<=a.top,"axis label separation");
    }
  }
}
check(!document.querySelector("[style]"),"static CSS only");
check(editorCalls.every(call=>call==="load"),"navigation never saves");
''', window_size=size), "PASS")

    def test_wheel_modes_focus_pan_capture_release_and_normal_activation(self) -> None:
        """DOM gestures consume navigation and preserve text/select controls and later clicks."""
        self.assertEqual(run_editor_harness(COMMON + r'''
await loadBounds(makeBounds(0,0,20480,20480));
const rectangle=region.getBoundingClientRect();
const wheel=(deltaY,deltaMode=0,ctrlKey=false)=>{
  const event=new WheelEvent("wheel",{deltaY,deltaMode,ctrlKey,clientX:rectangle.left+130,
    clientY:rectangle.top+90,bubbles:true,cancelable:true});region.dispatchEvent(event);return event;
};
for(const [mode,delta] of [[0,-100],[1,-2],[2,-.1]]) {
  view.fit();region.focus();const before=view.snapshot().scale;
  const pixels=mode===1?delta*parseFloat(getComputedStyle(region).lineHeight):(mode===2?delta*region.clientHeight:delta);
  check(wheel(delta,mode,true).defaultPrevented,"Ctrl wheel accepted");
  close(view.snapshot().scale,before*Math.pow(1.2,-pixels/100),"actual wheel mode conversion");
}
document.getElementById("map-show-view").click();
const select=document.getElementById("map-coordinate-mode");select.focus();const before=view.snapshot().scale;
check(!wheel(-100).defaultPrevented&&view.snapshot().scale===before,"focused select retains wheel");
const key=new KeyboardEvent("keydown",{key:" ",code:"Space",bubbles:true,cancelable:true});select.dispatchEvent(key);
check(!key.defaultPrevented,"select keeps Space activation");
const captured=new Set();let capture=0,release=0,placements=0;
region.setPointerCapture=id=>{captured.add(id);capture++};region.hasPointerCapture=id=>captured.has(id);
region.releasePointerCapture=id=>{captured.delete(id);release++};
region.addEventListener("pointerdown",()=>placements++);region.addEventListener("click",()=>placements++);
const pointer=(type,button,x,y)=>region.dispatchEvent(new PointerEvent(type,{button,pointerId:8,
  clientX:rectangle.left+x,clientY:rectangle.top+y,bubbles:true,cancelable:true}));
for(const space of [false,true]) {
  region.focus();if(space)region.dispatchEvent(new KeyboardEvent("keydown",{code:"Space",bubbles:true,cancelable:true}));
  const start=view.snapshot();pointer("pointerdown",space?0:1,20,30);pointer("pointermove",space?0:1,-50,-60);
  const moved=view.snapshot();close(moved.offsetX,start.offsetX-70,"pan outside X");close(moved.offsetY,start.offsetY-90,"pan outside Y");
  pointer("pointerup",space?0:1,-50,-60);region.click();
  window.dispatchEvent(new KeyboardEvent("keyup",{code:"Space"}));
}
check(capture===2&&release===2&&!captured.size&&placements===0,"capture requests and pan consumption");
pointer("pointerdown",0,50,60);region.click();check(placements===2,"normal activation retained");
pointer("pointerdown",1,20,30);pointer("pointercancel",1,20,30);check(!captured.size,"cancel releases capture");
region.click();check(placements===3,"cancel does not consume later normal activation");
pointer("pointerdown",1,20,30);window.dispatchEvent(new Event("blur"));check(!captured.size,"window blur releases capture");
region.click();check(placements===4,"blur does not consume later normal activation");
'''), "PASS")

    def test_admissible_fit_threshold_tiny_extents_zero_view_and_overflow(self) -> None:
        """The admitted fit ceiling preserves tiny extents and truthful zero-view recovery."""
        self.assertEqual(run_editor_harness(COMMON + r'''
const tested=window.ServerManMapViewport.create(()=>{});tested.resize(400,200);
for(const width of [20*(1-Number.EPSILON),20,20*(1+Number.EPSILON)]) {
  tested.setBounds(makeBounds(0,0,width,10));const state=tested.snapshot();
  check(state.fitScale===Math.min(400/width,200/10,20),"direct fit threshold");
  tested.zoom({x:100,y:80},1e8);check(tested.snapshot().scale===state.fitScale/2,"admissible half fit");
  tested.fit();check(tested.snapshot().scale<=20,"Fit ceiling");
}
for(const bounds of [makeBounds(-2.25,3.125,1,2),makeBounds(0,0,1e-8,2e-8),makeBounds(0,0,1e-308,1e-308)]) {
  const original=JSON.stringify(bounds);tested.setBounds(bounds);tested.fit();
  check(tested.snapshot().fitScale===20&&tested.snapshot().scale===20,"tiny fit leaves unused space");
  check([tested.snapshot().offsetX,tested.snapshot().offsetY].every(Number.isFinite),"geometric-fit overflow stays finite");
  tested.resize(200,100);check(tested.snapshot().scale===20,"tiny resize respects ceiling");
  tested.zoom({x:50,y:40},1e8);check(tested.snapshot().scale===10,"tiny minimum");
  check(JSON.stringify(bounds)===original,"no extent minimum or stored rounding");
}
tested.resize(0,0);check(tested.snapshot().scale===0&&tested.toNative({x:0,y:0})===null&&!tested.fit(),"zero viewport unavailable");
const missingProjection=tested.toScreen({x:0,z:0});check(missingProjection.x===null&&missingProjection.y===null,"no projection from unmeasured scale");
tested.resize(400,200);check(tested.snapshot().scale===20,"measured viewport recovers");
const before=tested.snapshot();check(!tested.pan(Infinity,0),"nonfinite pan refused");
check(!tested.zoom({x:Infinity,y:0},-100),"nonfinite cursor refused");
check(!tested.pan(1e308,1e308)||!tested.pan(1e308,1e308),"overflow cannot enter view state");
check([tested.snapshot().offsetX,tested.snapshot().offsetY,tested.snapshot().scale].every(Number.isFinite),"finite state retained");
await loadBounds(makeBounds(0,0,1,2));
check(view.snapshot().scale===20&&Number(document.getElementById("map-terrain-surface").getAttribute("width"))===20,"actual tiny terrain size");
view.resize(0,0);check(document.getElementById("map-fit").disabled&&document.getElementById("map-coordinate-mode").disabled,"unmeasured controls unavailable");
check(document.getElementById("map-view-label").textContent.includes("not available"),"unmeasured view explained");
view.resize(region.clientWidth,region.clientHeight);check(!document.getElementById("map-fit").disabled,"actual measured view recovers");
'''), "PASS")

    def test_collapsed_native_axis_refuses_only_transform_dependencies(self) -> None:
        """D1 data stays admitted; checked targets cannot turn collapsed X into an edit."""
        from dayz_serverman.domain.mission_map_geometry import parse_terrain

        bounds = {"x_min": 1000000, "z_min": 0, "width": 5e-324, "height": 1000}
        self.assertEqual(parse_terrain({"terrain_id": "synthetic", "bounds": bounds,
            "bounds_source": "manual", "bounds_confirmed": True})["bounds"], bounds)
        self.assertEqual(run_editor_harness(COMMON + r'''
const failed=Object.freeze(makeBounds(1000000,0,Number.MIN_VALUE,1000));
const original=JSON.stringify(failed),axes=coordinates.axes(failed);
check(axes.x.includes("endpoint")&&axes.z===null,"exact failed axis and concrete reason");
const native=coordinates.fromGrid({h:5,v:5},failed),grid=coordinates.grid({x:1000000,z:500},failed);
check(native.x===null&&native.z===500&&grid.h===null&&grid.v===5,"valid Z survives pure conversion");
const direct=coordinates.display({x:1000000,z:500},failed,20,"Both");
check(direct.includes("X Not available")&&direct.includes("Z 500.0 m")&&direct.includes("Z 5.0"),"per-axis readout");
const tested=window.ServerManMapViewport.create(()=>{});tested.resize(400,200);
tested.setBounds(makeBounds(0,0,2000,1000));const usable=tested.snapshot();
tested.setBounds(failed);const refused=tested.snapshot();
check(JSON.stringify(refused.bounds)===original&&refused.failure.axes.x&&refused.failure.axes.z===null,"exact loaded bounds retained");
for(const action of [()=>tested.fit(),()=>tested.zoom({x:100,y:80},-100),()=>tested.pan(10,20)]) {
  check(action()===false,"dependent navigation is unavailable rather than a successful no-op");
}
check(["scale","offsetX","offsetY"].every(key=>tested.snapshot()[key]===usable[key]),"prior usable view preserved");
let committed=0;const target=tested.toNative({x:200,y:100});if(target)committed++;
check(target===null&&committed===0,"checked future coordinate-target seam cannot commit");
const projected=tested.toScreen({x:1000000,z:500});check(projected.x===null&&Number.isFinite(projected.y),"checked projection retains only valid Z");
const partial=tested.toNativeAxes({x:200,y:100});check(partial.x===null&&partial.z===500,"valid pointer Z remains available");
tested.resize(500,250);check(tested.snapshot().failure.axes.x&&tested.snapshot().offsetX===usable.offsetX,"failure reevaluated without fake fit");
await loadBounds(makeBounds(0,0,2000,1000));const drawing=document.getElementById("map-vector").innerHTML;
const geometry=Object.freeze({x:1000000,z:500,radius:2});
const retained=Object.freeze({terrain:Object.freeze({terrain_id:"synthetic",bounds:failed}),objects:Object.freeze([geometry])});
const saved=JSON.stringify(retained);editorAnswer={success:true,value:{...editorValue,plan:retained,read_only:false}};
await window.ServerManMapEditor.load();await wait();
check(document.getElementById("map-vector").innerHTML===drawing,"prior drawing preserved under explicit failure");
check(document.getElementById("map-fit").disabled&&!document.getElementById("map-coordinate-mode").disabled,"only dependent Fit disabled");
const reason=document.getElementById("map-view-label").textContent;
check(reason.includes(window.ServerManMapWording.conversionFailure)&&reason.includes(window.ServerManMapWording.conversionActions)&&reason.includes("X:"),"accepted operator wording and axis");
check(document.getElementById("map-editor").dataset.readOnly==="false"&&JSON.stringify(retained)===saved,"no blanket read-only or stored mutation");
check(document.getElementById("map-announcement").textContent===window.ServerManMapWording.unsaved,"independent loaded workflow remains available");
await window.ServerManMapEditor.settleForClose();window.ServerManMapEditor.releaseClose();
check(document.getElementById("map-fit").disabled&&view.snapshot().failure.axes.x,"dismissal cannot reenable failed navigation");
await loadBounds(makeBounds(0,0,1e-8,2e-8));check(!view.snapshot().failure&&view.snapshot().scale===20,"representable tiny bounds recover without minimum");
check(!document.getElementById("map-fit").disabled&&editorCalls.every(call=>call==="load"),"no save or unrelated API operation");
check(JSON.stringify(failed)===original,"exact bounds never repaired or rounded");
'''), "PASS")

    def test_modes_unknown_bounds_readout_announcements_and_reload(self) -> None:
        """Local mode survives reload, while unknown bounds and failures stay unavailable."""
        self.assertEqual(run_editor_harness(COMMON + r'''
check(document.getElementById("map-fit").disabled&&!document.getElementById("map-terrain-surface"),"no guessed bounds");
await loadBounds(makeBounds(0,0,20480,20480));const select=document.getElementById("map-coordinate-mode");
check(select.value==="Both"&&!select.disabled,"Both default for known bounds");
const announcement=document.getElementById("map-announcement").textContent;
const move=()=>{const point=view.toScreen({x:10240,z:10240}),rect=region.getBoundingClientRect();
  region.dispatchEvent(new PointerEvent("pointermove",{clientX:rect.left+region.clientLeft+point.x,
    clientY:rect.top+region.clientTop+point.y,pointerId:4,bubbles:true}));};
for(const mode of ["Grid","Native","Both"]) {
  select.value=mode;select.dispatchEvent(new Event("change"));move();
  const text=document.getElementById("map-coordinates").textContent;
  check(text.includes("X")&&text.includes("Z"),"X and Z readout");
  check(mode==="Grid"?text.includes("5.0"):text.includes("10240 m"),"correct mode readout");
  check(mode==="Both"?text.includes("\nGrid"):!text.includes("\n"),"Both shows both systems");
  await window.ServerManMapEditor.load();check(select.value===mode,"mode survives reload");
}
check(document.getElementById("map-announcement").textContent===announcement,"cursor never live announces");
check(getComputedStyle(document.getElementById("map-coordinates")).fontVariantNumeric==="tabular-nums","tabular coordinates");
editorAnswer={success:true,value:{...editorValue,plan:null}};await window.ServerManMapEditor.load();
check(select.value==="Both"&&select.disabled&&document.getElementById("map-fit").disabled,"unknown controls unavailable");
check(!document.getElementById("map-terrain-surface")&&document.getElementById("map-coordinates").textContent==="Not available","truthful unknown view");
editorAnswer={success:false,error:{code:"NOT_FOUND",message:"Missing synthetic profile"}};await window.ServerManMapEditor.load();
check(select.disabled&&document.getElementById("map-editor").getAttribute("aria-busy")==="false","failure remains truthful");
check(Object.keys(window.ServerManMapEditor).sort().join(",")==="load,releaseClose,settleForClose","public editor API unchanged");
check(editorCalls.every(call=>call==="load"),"display-only state never saves");
'''), "PASS")
