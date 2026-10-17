/*
 * Keyboard focus movement adapted from Waku ui.js (_menuKey).
 * Source: ShenSeanChen/waku-agent, 4b69b321324750f231fdfd72a83b38ad7301dcc3.
 * MIT License
 * Copyright (c) 2026 Sean Chen (ShenSeanChen)
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  function make(tag, text, parent) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (parent) parent.append(element);
    return element;
  }
  function moveFocus(event, items) {
    if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key) || !items.length) return;
    const i = items.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length-1 :
      ["ArrowDown", "ArrowRight"].includes(event.key) ? (i+1)%items.length : (i-1+items.length)%items.length;
    items[next].focus();
    event.preventDefault();
  }
  function time(value) {
    return value ? new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",dateStyle:"medium",timeStyle:"short"}).format(new Date(value)) : "尚未完成";
  }
  window.CTAUI = Object.freeze({$, make, moveFocus, time});
})();
