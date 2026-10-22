(function () {
  'use strict';

  function $(sel) { return document.querySelector(sel); }

  function openConsole(path) {
    try { localStorage.setItem('hermes-locale', 'zh'); } catch (_) {}
    const tab = $('#tabs button[data-view="console"]');
    if (tab) tab.click();
    window.setTimeout(function () {
      const frame = $('#console-frame');
      if (frame) frame.setAttribute('src', path);
    }, 30);
  }

  function openChat() {
    const tab = $('#tabs button[data-view="chat"]');
    if (tab) tab.click();
  }

  document.addEventListener('DOMContentLoaded', function () {
    const oauth = $('#ai-oauth');
    const models = $('#ai-models');
    const apiKey = $('#ai-apikey');
    const test = $('#ai-test');

    if (oauth) oauth.addEventListener('click', function () {
      openConsole('/env');
    });
    if (models) models.addEventListener('click', function () {
      openConsole('/models');
    });
    if (apiKey) apiKey.addEventListener('click', function () {
      openConsole('/env');
    });
    if (test) test.addEventListener('click', openChat);
  });
})();
