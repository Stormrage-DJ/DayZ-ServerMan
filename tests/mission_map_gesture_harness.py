"""Shared synthetic selection and modifier-aware pointer gestures for editor Edge tests."""

SELECT = r'''
const objects=()=>model.snapshot().objects,find=(id)=>objects().find(item=>item.id===id),geometry=(id)=>JSON.stringify(find(id));
const entries=()=>model.status().history.length,position=()=>model.status().history.position;
const selected=()=>window.ServerManMapLayers.selected(),zone=(id)=>document.querySelector(`[data-map-zone="${id}"]`);
const pick=(id)=>document.querySelector(`[data-map-control="${id}:select"]`).click();
const handles=()=>document.querySelectorAll("#map-zones .map-handle").length;
const circle=(id,x,z,radius,extra={})=>({id,kind:"loot_circle",name:`Loot circle ${id}`,visible:true,locked:false,
  centre:{x,z},radius,tier:null,abundance:"rich",...extra});
const polygon=(id,vertices,extra={})=>({id,kind:"loot_polygon",name:`Loot polygon ${id}`,visible:true,locked:false,
  vertices:vertices.map(([x,z])=>({x,z})),tier:null,abundance:"rich",...extra});
const key=(target,name,extra={})=>{target.focus();target.dispatchEvent(new KeyboardEvent("keydown",{key:name,bubbles:true,cancelable:true,...extra}))};
// Pointer events as a mouse sends them: the primary button is down (buttons 1) until the release
const gesture=(type,place,id=7,buttons=type==="pointerup"?0:1,target=region,ctrl=false)=>target.dispatchEvent(new PointerEvent(type,
  {...place,button:type==="pointermove"?-1:0,buttons,pointerId:id,ctrlKey:ctrl,bubbles:true,cancelable:true}));
const release=(place,id,ctrl=false)=>{gesture("pointerup",place,id,0,region,ctrl);region.dispatchEvent(new MouseEvent("click",{...place,button:0,bubbles:true,cancelable:true}))};
// Press at one native point, move in steps to another, and release unless asked to hold
const holdControl=held=>{region.focus();region.dispatchEvent(new KeyboardEvent(held?"keydown":"keyup",{key:"Control",ctrlKey:held,bubbles:true}));};
const drag=(from,to,{steps=10,id=7,hold=false,ctrl=false}={})=>{holdControl(ctrl);const a=at(...from),b=at(...to);gesture("pointerdown",a,id,1,region,ctrl);
  for(let step=1;step<=steps;step++)gesture("pointermove",{clientX:a.clientX+(b.clientX-a.clientX)*step/steps,clientY:a.clientY+(b.clientY-a.clientY)*step/steps},id,1,region,ctrl);
  if(!hold)release(b,id,ctrl);return b};
const near=(value,expected)=>Math.abs(value-expected)<=2/view.snapshot().scale;
'''
