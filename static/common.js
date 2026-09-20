"use strict";
const $=selector=>document.querySelector(selector);
const $$=selector=>[...document.querySelectorAll(selector)];
async function api(path,options={}){const response=await fetch(path,{headers:{"Content-Type":"application/json"},...options});const data=await response.json();if(!response.ok)throw new Error(data.error||"Erreur");return data}
function esc(value){return String(value??"").replace(/[&<>"']/g,character=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]))}
function notify(message,error=false){const box=$("#toast");box.textContent=message;box.classList.toggle("error",error);box.hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>box.hidden=true,4500)}
function askConfirm(message){const dialog=$("#confirm-dialog");$("#confirm-message").textContent=message;dialog.showModal();return new Promise(resolve=>dialog.addEventListener("close",()=>resolve(dialog.returnValue==="confirm"),{once:true}))}
function setBusy(button,busy,label="Traitement…"){if(busy){button.dataset.label=button.textContent;button.textContent=label;button.disabled=true;button.setAttribute("aria-busy","true")}else{button.textContent=button.dataset.label||button.textContent;button.disabled=false;button.removeAttribute("aria-busy")}}
function page(id){$$('.page,nav button').forEach(element=>element.classList.remove("active"));$$('nav button').forEach(button=>button.removeAttribute("aria-current"));$("#"+id).classList.add("active");const button=$(`nav button[data-page="${id}"]`);button?.classList.add("active");button?.setAttribute("aria-current","page");$("#main-content").focus();if(id==="companies"||id==="spontaneous")loadEstablishments();if(id==="spontaneous")loadSpontaneousResumes();if(id==="offers")loadOffers();if(id==="trash")loadTrash();if(id==="resumes"){loadResumes();loadBackups()}if(id==="statistics")loadStatistics();if(id==="profile")loadConfiguration()}
$$('nav button').forEach(button=>button.onclick=()=>page(button.dataset.page));$$('[data-go]').forEach(button=>button.onclick=()=>page(button.dataset.go));
$("#theme").onclick=()=>{document.body.classList.toggle("dark");localStorage.theme=document.body.classList.contains("dark")?"dark":"light"};if(localStorage.theme==="dark")document.body.classList.add("dark");
$("#font").onclick=()=>document.body.classList.toggle("large");
