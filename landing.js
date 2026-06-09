/* CreatorCRM landing — vanilla JS, no build step */
(function () {
  'use strict';

  /* ── Sticky nav shrink ── */
  var nav = document.getElementById('nav');
  var onScroll = function () {
    if (window.scrollY > 12) nav.classList.add('scrolled');
    else nav.classList.remove('scrolled');
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ── Mobile menu ── */
  var burger = document.getElementById('burger');
  var navMobile = document.getElementById('navMobile');
  if (burger) {
    burger.addEventListener('click', function () {
      navMobile.classList.toggle('open');
    });
    navMobile.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function () { navMobile.classList.remove('open'); });
    });
  }

  /* ── Scroll reveals (with stagger via data-delay) ── */
  var reveals = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          var d = parseInt(e.target.getAttribute('data-delay') || '0', 10);
          setTimeout(function () { e.target.classList.add('in'); }, d);
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
    reveals.forEach(function (el) { io.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add('in'); });
  }

  /* ── Animated number counters ── */
  var counted = false;
  var animateCounts = function () {
    document.querySelectorAll('[data-count]').forEach(function (el) {
      var target = parseFloat(el.getAttribute('data-count'));
      var dur = 1100, start = null;
      var step = function (ts) {
        if (!start) start = ts;
        var p = Math.min((ts - start) / dur, 1);
        var eased = 1 - Math.pow(1 - p, 3);
        el.textContent = Math.round(target * eased).toLocaleString();
        if (p < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
  };
  var counterEl = document.querySelector('[data-count]');
  if (counterEl && 'IntersectionObserver' in window) {
    var cio = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && !counted) { counted = true; animateCounts(); }
      });
    }, { threshold: 0.5 });
    cio.observe(counterEl);
  }

  /* ── FAQ accordion: close others on open ── */
  var dets = document.querySelectorAll('.acc details');
  dets.forEach(function (d) {
    d.addEventListener('toggle', function () {
      if (d.open) dets.forEach(function (o) { if (o !== d) o.open = false; });
    });
  });
})();
