const slider=document.querySelector('#budget');const shown=document.querySelector('#shown');
const budgetValue=document.querySelector('#budget-value');const cards=[...document.querySelectorAll('.attention')];
const critical=document.querySelector('#critical-left');const curve=[...document.querySelectorAll('.curve-point')];
function applyBudget(){const budget=Number(slider.value);budgetValue.textContent=budget;
const point=curve.find(item=>Number(item.dataset.budget)===budget);const ids=new Set(point?point.dataset.ids.split(',').filter(Boolean):[]);
let count=0;for(const card of cards){const fits=ids.has(card.dataset.evidence);card.hidden=!fits;if(fits)count+=1}
shown.textContent=count;critical.textContent=point?point.dataset.critical:'unknown'}
if(slider){slider.addEventListener('input',applyBudget);applyBudget()}
