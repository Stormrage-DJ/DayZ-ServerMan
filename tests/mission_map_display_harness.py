"""Shared synthetic plan, tier masks, image and fake display reads of the 3.15 and 3.16 Edge tests."""

from __future__ import annotations

import base64
import hashlib

from dayz_serverman.domain.mission_map_png import tier_png
from dayz_serverman.domain.mission_map_terrain import ValueRows
from tests.mission_map_image_support import png_rgb
from tests.test_mission_map_terrain import areaflags_bytes, header_of


def _masks() -> dict[str, str]:
    """Return base64 PNG masks of a 4 × 4 grid: Tier1 at cell (0, 0) (south-west), Tier2 at cell (3, 3)."""
    data = areaflags_bytes(4, 4, 1000, 1000, 8, 4, {(0, 0): 0b01, (3, 3): 0b10})
    header = header_of(data)
    rows = ValueRows(header, 0, 4, data[header.value_offset:])
    return {name: base64.b64encode(tier_png(rows, mask)).decode("ascii") for name, mask in (("Tier1", 1), ("Tier2", 2))}


def _quadrant(x: int, y: int) -> tuple[int, int, int]:
    """Quadrant colours with y = 0 at the top: red north-west, green north-east, blue south-west, white south-east."""
    return [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 255)]][y // 50][x // 50]


# A 100 × 100 image in two parts; the split is a multiple of 3 bytes, so the base64 texts join
IMAGE = png_rgb(100, 100, _quadrant)
SPLIT = len(IMAGE) // 2 // 3 * 3
MASKS = _masks()

COMMON = r'''
const association={profile_id:"alpha",mission_root:"mpmissions\\dayzOffline.synthetic",mission_key:"k".repeat(32),runtime_profile_key:null};
const masks=__MASKS__;
const image={sha256:"__SHA__",byte_size:__SIZE__,parts:["__PART0__","__PART1__"]};
const plan=(background=null)=>({format:"dayz-serverman/mission-map-plan",version:1,plan_id:"stable-plan",
  association:structuredClone(association),terrain:{terrain_id:"synthetic",bounds:{x_min:0,z_min:0,width:1000,height:1000},
  bounds_source:"manual",bounds_confirmed:true},global_abundance:0,objects:[],background,records:{originals:[],baseline:null,applied:null}});
const capabilities=(holdable=["Tier1","Tier2"],unsupported=[])=>({terrain_id:"synthetic",
  header:holdable.length?{columns:4,rows:4,world_width:1000,world_height:1000,value_bits:4}:null,detected_bounds:null,
  holdable_tiers:holdable,unsupported,tier_writes:{available:false,reason:"bounds_not_confirmed"}});
const value=(document=plan(),caps=capabilities())=>({...editorValue,association:structuredClone(association),plan:document,
  revision:4,state:"VALID",capabilities:caps,draft_changes:false});
const refuse=(code,details)=>({success:false,error:{code,message:"Refused.",retryable:false,details}});
const tierReads=[],imageReads=[],saved=[];
const tierAnswers={};
for(const [tier,png] of Object.entries(masks))tierAnswers[tier]=()=>({success:true,value:{tier,columns:4,rows:4,
  world_width:1000,world_height:1000,areaflags_sha256:"f".repeat(64),byte_size:png.length,png}});
window.pywebview.api.read_tier_layer=async(key,tier)=>{tierReads.push([key,tier]);
  return tierAnswers[tier]?tierAnswers[tier]():refuse("INVALID_REQUEST",{reason:"tier_unknown"});};
const part=(index,extra={})=>({available:true,file_name:"map.png",media_type:"image/png",sha256:image.sha256,
  byte_size:image.byte_size,pixel_width:100,pixel_height:100,part:index,parts:2,data:image.parts[index],...extra});
const imageAnswers={stored:(index)=>({success:true,value:part(index)}),chosen:(index)=>({success:true,value:part(index)}),
  select:()=>({success:true,value:{...part(0),selection_id:"token-1"}})};
window.pywebview.api.read_background=async(key,selection,index)=>{imageReads.push([key,selection,index]);
  return selection===null?imageAnswers.stored(index):imageAnswers.chosen(index);};
window.pywebview.api.select_background=async(key)=>{imageReads.push([key,"select"]);return imageAnswers.select();};
window.pywebview.api.save_plan=async(document,revision,selection)=>{saved.push({plan:structuredClone(document),revision,selection});
  return {success:true,value:{association:structuredClone(association),revision:revision+1,draft_changes:true,
  fingerprint:"b".repeat(64),interrupted_evidence:[]}};};
// Headless virtual time can use up its timers before an off-thread image decode ends;
// message hops keep virtual time still while real time passes, so the decode can finish
const hop=()=>new Promise((resolve)=>{const channel=new MessageChannel();channel.port1.onmessage=()=>resolve();channel.port2.postMessage(0);});
const until=async(condition,text)=>{for(let index=0;index<150;index++){if(condition())return;await wait();
  for(let step=0;step<2000&&!condition();step++)await hop();}throw new Error(text);};
const load=async(current)=>{editorAnswer={success:true,value:current};await window.ServerManMapEditor.load();await wait();};
const view=()=>window.ServerManMapViewportUi.view();
// Average the raster pixels of a 7 × 7 block around a native point; hatch pixels average out
const pixel=(x,z)=>{const point=view().toScreen({x,z}),ratio=window.devicePixelRatio||1;
  const data=document.getElementById("map-raster").getContext("2d").getImageData(Math.round(point.x*ratio)-3,
    Math.round(point.y*ratio)-3,7,7).data;const sum=[0,0,0];
  for(let index=0;index<data.length;index+=4)for(let channel=0;channel<3;channel++)sum[channel]+=data[index+channel];
  return sum.map((total)=>Math.round(total/49));};
const near=(actual,expected,tolerance,text)=>check(actual.every((channel,index)=>Math.abs(channel-expected[index])<=tolerance),
  `${text}: ${actual} against ${expected}`);
const terrain=[28,38,50];
const text=(id)=>document.getElementById(id).textContent;
// The document starts its own load at DOMContentLoaded; let it settle so each test counts only its own reads
await new Promise((resolve)=>document.readyState==="loading"?document.addEventListener("DOMContentLoaded",resolve,{once:true}):resolve());
await until(()=>editorCalls.length===1&&document.getElementById("map-editor").getAttribute("aria-busy")==="false","start-up load settled");
'''


def common() -> str:
    """Return the shared harness prelude with the generated masks and image parts."""
    replacements = {
        "__MASKS__": "{" + ",".join(f'{name}:"{data}"' for name, data in MASKS.items()) + "}",
        "__SHA__": hashlib.sha256(IMAGE).hexdigest(), "__SIZE__": str(len(IMAGE)),
        "__PART0__": base64.b64encode(IMAGE[:SPLIT]).decode("ascii"),
        "__PART1__": base64.b64encode(IMAGE[SPLIT:]).decode("ascii"),
    }
    text = COMMON
    for marker, replacement in replacements.items():
        text = text.replace(marker, replacement)
    return text
