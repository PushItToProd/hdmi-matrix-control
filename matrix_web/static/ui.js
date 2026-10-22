document.body.addEventListener('htmx:sendError', () => {
  document.getElementById('connection-error').hidden = false;
});
document.body.addEventListener('htmx:responseError', () => {
  document.getElementById('connection-error').hidden = false;
});
document.body.addEventListener('htmx:afterRequest', event => {
  if (event.detail.successful) document.getElementById('connection-error').hidden = true;
});
document.body.addEventListener('htmx:load', () => {
  document.querySelectorAll('time[datetime]').forEach(element => {
    const parts = new Intl.DateTimeFormat(undefined, {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
    }).formatToParts(new Date(element.dateTime));
    const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
    element.textContent = `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
  });
});
