'use strict';
// English is the source language, so a missing translation still reads correctly.
const currentLanguage = document.documentElement.lang === 'ru' ? 'ru' : 'en';
const RU = {};

function locale() {
    return currentLanguage === 'ru' ? 'ru-RU' : 'en-GB';
}

function pluralIndex(value) {
    const n = Math.abs(Math.trunc(value)),
        tens = n % 100,
        units = n % 10;
    if (tens >= 11 && tens <= 14) return 2;
    if (units === 1) return 0;
    return units >= 2 && units <= 4 ? 1 : 2;
}

function plainNumber(value) {
    return Number(String(value ?? '').replace(/<[^>]*>/g, '').replace(/[^\d]/g, '') || 0);
}

function t(key, params = {}) {
    let text = currentLanguage === 'ru' ? (RU[key] ?? key) : key;
    if (Array.isArray(text)) text = text[pluralIndex(plainNumber(params.n))];
    return text.replace(/\{(\w+)\}/g, (match, name) => name in params ? params[name] : match);
}

function number(value) {
    if (value === null || value === undefined) return null;
    return new Intl.NumberFormat(locale()).format(value);
}

function decimal(value) {
    return new Intl.NumberFormat(locale(), {
        maximumFractionDigits: 1
    }).format(value);
}

function capitalise(text) {
    return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function currencySign(code) {
    return {
        KZT: '₸',
        USD: '$',
        EUR: '€',
        RUB: '₽'
    } [code] ?? code;
}

function setLanguage(language) {
    try {
        localStorage.setItem('sentient.lang', language)
    } catch {}
    location.reload();
}

// Static markup is written in English and swapped once at start-up.
function translateStatic() {
    if (currentLanguage === 'en') return;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
        if (node.parentElement.closest('script,style')) continue;
        const text = node.nodeValue.trim();
        if (text && RU[text] && !Array.isArray(RU[text])) node.nodeValue = node.nodeValue.replace(text, RU[text]);
    }
    for (const element of document.querySelectorAll('[placeholder],[aria-label],[title]')) {
        for (const name of ['placeholder', 'aria-label', 'title']) {
            const value = element.getAttribute(name);
            if (value && RU[value]) element.setAttribute(name, RU[value]);
        }
    }
    document.title = 'Sentient · Аналитика торгового зала';
}
document.addEventListener('DOMContentLoaded', translateStatic);
