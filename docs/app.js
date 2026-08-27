const slider=document.querySelector('#budget');const shown=document.querySelector('#shown');
const budgetValue=document.querySelector('#budget-value');const cards=[...document.querySelectorAll('.attention')];
const critical=document.querySelector('#critical-left');const curve=[...document.querySelectorAll('.curve-point')];
const budgetPanel=document.querySelector('.controls');const pageBudget=document.querySelector('#page-budget');
function applyBudget(){if(!slider)return;const budget=Number(slider.value);budgetValue.textContent=budget;
const point=curve.find(item=>Number(item.dataset.budget)===budget);const ids=new Set(point?point.dataset.ids.split(',').filter(Boolean):[]);
let count=0;for(const card of cards){const fits=ids.has(card.dataset.evidence);card.hidden=!fits;if(fits)count+=1}
shown.textContent=count;critical.textContent=point?point.dataset.critical:'—';
if(pageBudget)pageBudget.textContent=`${budget.toLocaleString()} UNIT PAGE`;
budgetPanel?.style.setProperty('--budget-progress',`${((budget-300)/1500)*100}%`)}
if(slider){slider.addEventListener('input',applyBudget);applyBudget()}
const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if(!reduced&&'IntersectionObserver'in window){const observer=new IntersectionObserver(entries=>{for(const entry of entries){if(entry.isIntersecting){entry.target.classList.add('in');observer.unobserve(entry.target)}}},{threshold:.12});for(const item of document.querySelectorAll('.reveal'))observer.observe(item)}else{for(const item of document.querySelectorAll('.reveal'))item.classList.add('in')}
