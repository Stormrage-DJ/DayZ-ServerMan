// Fake storage for the one complete configuration draft and one Apply operation.
const networkCalls = {saves: [], previews: [], applied: []};
const networkValues = Object.fromEntries(profiles.map(p => [p.profile_id, {steamQueryPort:p.profile_id === "alpha" ? 2305 : 2405, hostname:p.profile_id}]));
let networkDigest = "original-file", networkApplyNumber = 0;
window.pywebview.api.save_profile = async (...args) => { networkCalls.saves.push(args); throw new Error("unexpected profile save"); };
window.pywebview.api.load_configuration = async profileId => ok({profile_id:profileId, target:"server", relative_path:`${profileId}.cfg`,
  profile_revision:profiles.find(p=>p.profile_id===profileId).revision, settings_revision:2, digest:networkDigest, server_config_digest:null,
  fields:[{key:"game_port",kind:"integer",present:true,value:profiles.find(p=>p.profile_id===profileId).game_port,secret:false},
    ...Object.entries(networkValues[profileId]).map(([key,value])=>({key,value,kind:key==="hostname"?"string":"integer",present:true,secret:false}))]});
window.pywebview.api.preview_configuration = async (...args) => {
  networkCalls.previews.push(args);
  const p=profiles.find(p=>p.profile_id===args[0]), values={...networkValues[p.profile_id],game_port:p.game_port};
  return ok({profile_id:p.profile_id,changed_fields:Object.keys(args[6]),
    changes:Object.entries(args[6]).map(([key,after])=>({key,before:values[key],after}))});
};
window.pywebview.api.apply_configuration = async (...args) => {
  networkCalls.applied.push(args);const id=`network-apply-${++networkApplyNumber}`;
  host.operations.set(id,record(id,"APPLY_CONFIGURATION","QUEUED"));return ok({operation_id:id,state:"QUEUED"});
};
const networkEdit=(id,value)=>{const input=byId(id);input.value=value;input.dispatchEvent(new Event("input",{bubbles:true}));};
const networkAction=async()=>{byId("configuration-apply").click();await wait(45);};
const networkFinish=async()=>{
  const args=networkCalls.applied.at(-1),p=profiles.find(p=>p.profile_id===args[0]);
  for(const [key,value] of Object.entries(args[6])) {
    if(key==="game_port") {if(p.game_port!==value)p.revision+=1;p.game_port=value;}
    else networkValues[p.profile_id][key]=value;
  }
  networkDigest="saved-"+networkApplyNumber;
  await push(record(`network-apply-${networkApplyNumber}`,"APPLY_CONFIGURATION","SUCCEEDED",{result:{}}));await wait(80);
};

async function networkSaveScenario() {
  await start("configuration");await wait(60);
  const game=byId("configuration-game-port"),query=byId("configuration-steamQueryPort");
  check(game.value==="2302"&&query.value==="2305","real loaded values");
  check(game.closest("fieldset")===query.closest("fieldset"),"one Network card");
  check(!byId("configuration-game-save")&&!byId("configuration-game-discard"),"no separate port actions");
  check(!window.ServerManTransitions.hasUnsavedChanges()&&networkCalls.saves.length===0,"opening changes nothing");
  for(const invalid of ["", "0", "65536", "1.5"]) {networkEdit(game.id,invalid);check(byId("configuration-apply").disabled,"invalid game port blocks Review");}
  for(const valid of ["1","65535"]) {networkEdit(game.id,valid);check(!byId("configuration-apply").disabled,"inclusive game port endpoints");}
  networkEdit(game.id,"2502");await networkAction();
  check(JSON.stringify(networkCalls.previews.at(-1)[6])==='{"game_port":2502}',"game-only review uses shared payload");
  let text=byId("configuration-feedback").textContent;
  check(text.includes("Game port")&&text.includes("2302")&&text.includes("2502")&&text.includes("Alpha"),"real old/new values and selected profile");
  check(networkCalls.applied.length===0&&profiles[0].game_port===2302,"Review never writes");
  await networkAction();check(networkCalls.applied.length===1&&profiles[0].game_port===2302,"queue acceptance is pending");
  await networkFinish();check(byId(game.id).value==="2502"&&configurationState.loaded.profile_revision===4,"terminal success refreshes profile and configuration");
  check(!window.ServerManTransitions.hasUnsavedChanges(),"complete draft clears on success");
  networkEdit(game.id,"2602");networkEdit(query.id,"2605");networkEdit("configuration-hostname","First name");
  await networkAction();text=byId("configuration-feedback").textContent;
  check(["Game port","Steam query port","Server name","2502","2602","2305","2605","First name"].every(v=>text.includes(v)),"one review displays all mixed fields");
  networkEdit("configuration-hostname","Final name");
  check(!configurationState.reviewed&&byId("configuration-apply").textContent==="Review changes","any edit invalidates whole review");
  await networkAction();await networkAction();
  check(JSON.stringify(networkCalls.applied.at(-1)[6])==='{"game_port":2602,"hostname":"Final name","steamQueryPort":2605}',"one Apply contains all reviewed fields");
  await networkFinish();
  networkEdit(game.id,"2702");networkEdit(query.id,"2705");networkEdit("configuration-hostname","Discard me");
  byId("configuration-discard").click();
  check(byId(game.id).value==="2602"&&byId(query.id).value==="2605"&&byId("configuration-hostname").value==="Final name","one Discard restores every field");
  networkEdit(query.id,"2805");await networkAction();await networkAction();
  check(!("game_port" in networkCalls.applied.at(-1)[6]),"native-only request keeps legacy payload");await networkFinish();
  check(networkCalls.saves.length===0,"no direct profile saves");
  commitSection("profiles");await wait(50);
  const summary=document.querySelector('#profile-form [name="game_port"]');check(summary.readOnly,"immutable profile summary");summary.value="9999";
  check(captureProfile(document.getElementById("profile-form")).game_port===2602,"summary tampering ignored");
}

async function networkConflictScenario() {
  await start("configuration");await wait(60);
  const originalPreview=window.pywebview.api.preview_configuration;
  let release;window.pywebview.api.preview_configuration=()=>new Promise(resolve=>release=resolve);
  networkEdit("configuration-game-port","2502");byId("configuration-apply").click();await wait(10);
  networkEdit("configuration-steamQueryPort","2505");release(ok({changed_fields:["game_port"]}));await wait(30);
  check(!configurationState.reviewed&&networkCalls.applied.length===0,"late review cannot cover a newer draft");
  window.pywebview.api.preview_configuration=originalPreview;await networkAction();
  const originalApply=window.pywebview.api.apply_configuration;
  window.pywebview.api.apply_configuration=async()=>fail("REVISION_CONFLICT","Configuration changed after review.");await networkAction();
  check(byId("configuration-game-port").value==="2502"&&byId("configuration-steamQueryPort").value==="2505"&&!configurationState.reviewed,"stale Apply preserves all attempted values and requires review");
  window.pywebview.api.apply_configuration=originalApply;await networkAction();await networkAction();
  await push(record(`network-apply-${networkApplyNumber}`,"APPLY_CONFIGURATION","FAILED",{terminal_error:{code:"STORAGE_FAILURE",message:"Failed"}}));await wait(25);
  check(byId("configuration-game-port").value==="2502"&&!byId("configuration-game-port").disabled&&!configurationState.reviewed,"terminal failure preserves editable complete draft");
  let rejectLate;window.pywebview.api.apply_configuration=()=>new Promise((_resolve,reject)=>rejectLate=reject);
  await networkAction();byId("configuration-apply").click();await wait(10);
  profileContextState.selectedId="bravo";commitSection("configuration");await wait(60);
  rejectLate(new Error("old workspace request"));await wait(30);
  check(configurationState.loaded.profile_id==="bravo"&&byId("configuration-steamQueryPort").value==="2405"&&!configurationState.pendingContext,"late rejected Apply cannot change a new profile");
  window.pywebview.api.apply_configuration=originalApply;
  networkEdit("configuration-game-port","2902");await networkAction();await networkAction();
  const list=window.pywebview.api.list_profiles;let finishCatalog;
  window.pywebview.api.list_profiles=()=>{window.pywebview.api.list_profiles=list;return new Promise(resolve=>finishCatalog=resolve);};
  await push(record(`network-apply-${networkApplyNumber}`,"APPLY_CONFIGURATION","SUCCEEDED",{result:{}}));await wait(15);
  profileContextState.selectedId="alpha";commitSection("configuration");await wait(60);
  const stale=structuredClone(profiles);stale[0].game_port=9999;finishCatalog(ok(stale));await wait(30);
  check(configurationState.loaded.profile_id==="alpha"&&byId("configuration-game-port").value==="2302"
    &&window.ServerManProfileContext.profiles()[0].game_port===2302,"stale terminal catalog cannot overwrite a newer workspace");
  check(networkCalls.saves.length===0,"no hidden profile save path");
}
