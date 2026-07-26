const bridge = window.AstrBotPluginPage;
const bands = ["高好感 / 信任圈", "朋友", "普通熟人", "保持距离", "边界警戒"];
function $(selector){return document.querySelector(selector)}
function escapeHtml(value){return String(value ?? "").replace(/[&<>\"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]))}
function formatTime(ts){if(!ts)return "—"; const d=new Date(ts*1000); return isNaN(d)?"—":d.toLocaleString("zh-CN",{hour12:false})}
function toast(message,error=false){const el=$("#toast");el.textContent=message;el.classList.toggle("error",error);el.classList.remove("hidden");clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.add("hidden"),3000)}
function render(payload){
  const summary=payload.summary||{}; const policy=payload.policy||{};
  document.querySelectorAll("[data-stat]").forEach(el=>el.textContent=summary[el.dataset.stat]??0);
  document.querySelectorAll("[data-policy]").forEach(el=>el.textContent=policy[el.dataset.policy]??"—");
  const counts=summary.bands||{}; const max=Math.max(1,...bands.map(name=>counts[name]||0));
  $("#band-chart").innerHTML=bands.map(name=>`<div class="band-row"><div class="band-label">${escapeHtml(name)}</div><div class="bar-track"><div class="bar-fill" style="width:${((counts[name]||0)/max*100).toFixed(1)}%"></div></div><div class="band-count">${counts[name]||0}</div></div>`).join("");
  const users=payload.users||[]; const tbody=$("#relation-tbody");
  if(!users.length){tbody.innerHTML='<tr class="empty-row"><td colspan="9">暂无关系记录</td></tr>';return}
  tbody.innerHTML=users.map(user=>`<tr><td>${escapeHtml(user.user_id)}</td><td>${escapeHtml(user.band)}</td><td>${user.affinity}</td><td>${user.trust}</td><td>${user.familiarity}</td><td>${user.interaction_count}</td><td>${user.whitelisted?'<span class="badge ok">白名单</span>':'<span class="badge">普通</span>'}</td><td>${user.boundary==="开放"?'<span class="badge safe">开放</span>':'<span class="badge warn">谨慎</span>'}</td><td>${formatTime(user.last_event_at)}</td></tr>`).join("");
}
async function load(){try{const data=await bridge.apiGet("overview");render(data)}catch(error){toast(`加载关系状态失败：${error.message}`,true);$("#relation-tbody").innerHTML='<tr class="empty-row"><td colspan="9">加载失败</td></tr>'}}
$("#btn-refresh").addEventListener("click",load);load();
