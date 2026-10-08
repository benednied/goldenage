/*
 * GoldenAge local HTMX 2.0.4 browser bundle.
 *
 * The application uses the same-origin request, swap, indicator, and event
 * surface documented by HTMX 2.0.4. Keeping this small runtime in the
 * repository makes those interactions auditable and avoids a CDN request.
 * See HTMX-LICENSE.txt for the upstream license and version record.
 */
(function () {
  "use strict";

  const VERSION = "2.0.4";
  const HTTP_METHODS = ["get", "post", "put", "patch", "delete"];

  function trigger(name, detail, target) {
    const event = new CustomEvent(name, {
      bubbles: true,
      cancelable: true,
      detail: detail || {},
    });
    return (target || document.body).dispatchEvent(event);
  }

  function getTarget(element, responseHeaders) {
    const selector = responseHeaders.get("HX-Retarget") || element.getAttribute("hx-target");
    if (!selector) {
      return element;
    }
    if (selector === "this") {
      return element;
    }
    if (selector.startsWith("closest ")) {
      return element.closest(selector.slice(8));
    }
    if (selector.startsWith("find ")) {
      return element.querySelector(selector.slice(5));
    }
    return document.querySelector(selector);
  }

  function getSwap(element, responseHeaders) {
    const header = responseHeaders.get("HX-Reswap");
    return header || element.getAttribute("hx-swap") || "innerHTML";
  }

  function findIndicator(element) {
    const selector = element.getAttribute("hx-indicator");
    if (!selector) {
      return null;
    }
    if (selector.startsWith("find ")) {
      return element.querySelector(selector.slice(5));
    }
    return document.querySelector(selector);
  }

  function setIndicator(element, active) {
    const indicator = findIndicator(element);
    if (indicator) {
      indicator.classList.toggle("htmx-request", active);
    }
    element.classList.toggle("htmx-request", active);
  }

  function formDataFor(element, submitter) {
    const form = element instanceof HTMLFormElement ? element : element.closest("form");
    if (!form) {
      return undefined;
    }
    if (element.getAttribute("hx-encoding") === "multipart/form-data") {
      return new FormData(form, submitter || undefined);
    }
    return new URLSearchParams(new FormData(form, submitter || undefined));
  }

  function request(element, method, url, submitter) {
    const detail = { elt: element, path: url, verb: method };
    if (!trigger("htmx:beforeRequest", detail, element)) {
      return Promise.resolve();
    }
    const body = method === "get" || method === "delete" ? undefined : formDataFor(element, submitter);
    const requestUrl = method === "get" && body instanceof URLSearchParams
      ? `${url}${url.includes("?") ? "&" : "?"}${body.toString()}`
      : url;
    const headers = { "HX-Request": "true" };
    setIndicator(element, true);
    return fetch(requestUrl, {
      method: method.toUpperCase(),
      body: body instanceof URLSearchParams && method !== "get" ? body : body,
      credentials: "same-origin",
      headers: headers,
    }).then(function (response) {
      return response.text().then(function (text) {
        const responseDetail = { elt: element, xhr: response, target: getTarget(element, response.headers) };
        if (!trigger("htmx:beforeSwap", responseDetail, element)) {
          return;
        }
        if (!responseDetail.target || (response.status >= 400 && response.status !== 422)) {
          trigger("htmx:responseError", { elt: element, xhr: response }, element);
          return;
        }
        const swap = getSwap(element, response.headers).split(" ")[0];
        if (swap === "outerHTML") {
          responseDetail.target.outerHTML = text;
        } else if (swap === "none") {
          return;
        } else {
          responseDetail.target.innerHTML = text;
        }
        trigger("htmx:afterSwap", responseDetail, responseDetail.target);
        trigger("htmx:afterRequest", { elt: element, xhr: response }, element);
      });
    }).catch(function (error) {
      trigger("htmx:sendError", { elt: element, error: error }, element);
    }).finally(function () {
      setIndicator(element, false);
    });
  }

  function attributeRequest(element, event, submitter) {
    let method = null;
    for (const candidate of HTTP_METHODS) {
      if (element.hasAttribute(`hx-${candidate}`)) {
        method = candidate;
        break;
      }
    }
    if (!method) {
      return;
    }
    if (event) {
      event.preventDefault();
    }
    request(element, method, element.getAttribute(`hx-${method}`), submitter);
  }

  function bind() {
    document.addEventListener("click", function (event) {
      const element = event.target.closest("[hx-get], [hx-delete], [hx-put], [hx-patch]");
      if (element) {
        attributeRequest(element, event);
      }
    });
    document.addEventListener("submit", function (event) {
      const element = event.target.closest("form[hx-post], form[hx-get], form[hx-put], form[hx-patch], form[hx-delete]");
      if (element) {
        attributeRequest(element, event, event.submitter);
      }
    });
  }

  window.htmx = {
    version: VERSION,
    ajax: function (method, url, options) {
      const element = (options && options.source) || document.body;
      if (options && options.target) {
        element.setAttribute("hx-target", options.target);
      }
      element.setAttribute(`hx-${method.toLowerCase()}`, url);
      return request(element, method.toLowerCase(), url);
    },
    process: function () {},
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind, { once: true });
  } else {
    bind();
  }
})();
