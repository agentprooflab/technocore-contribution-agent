const slider=document.querySelector('#budget');const shown=document.querySelector('#shown');
const budgetValue=document.querySelector('#budget-value');const cards=[...document.querySelectorAll('.attention')];
function applyBudget(){const budget=Number(slider.value);let used=0,count=0;budgetValue.textContent=budget;
for(const card of cards){const units=Number(card.dataset.units);const fits=used+units<=budget;card.hidden=!fits;if(fits){used+=units;count+=1}}shown.textContent=count}
if(slider){slider.addEventListener('input',applyBudget);applyBudget()}
