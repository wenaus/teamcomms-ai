/* Apply the installation's theme before styles and deferred editor scripts paint. */
'use strict';
(() => {
  const config=JSON.parse(document.querySelector('meta[name=teamcomms-config]').content);
  let choice='system';
  try {
    const saved=localStorage.getItem('teamcomms:'+config.prefix+':theme');
    if (['system','light','dark'].includes(saved)) choice=saved;
  } catch (error) {
    // Continue with the system theme; the browser shell reports storage errors.
    console.warn('TeamComms theme preference unavailable:',error);
  }
  document.documentElement.dataset.theme=choice==='system'
    ? (matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light') : choice;
})();
