// // Redirección por usuario al entrar al Desk (v15)
// // Mapea correo → URL o ruta Frappe
// (function () {
//   // EDITA aquí tus usuarios y destinos
//   const DESTINO = {
//     "kasistencia@equipo.igcaribe.com": "/app/kiosko-asistencia",   // URL absoluto
//   };
//
//   function rutaActual() {
//     try { return (frappe.get_route_str() || "").toLowerCase(); } catch { return ""; }
//   }
//
//   function usuarioActual() {
//     try { return (frappe.session && frappe.session.user || "").toLowerCase(); } catch { return ""; }
//   }
//
//   function debeRedirigir() {
//     if (window.__did_user_redirect) return false;
//     const u = usuarioActual();
//     if (!u || !DESTINO[u]) return false;
//
//     const r = rutaActual();
//     // Solo intercepta el primer aterrizaje típico del Desk
//     const esDeskInicial =
//       !r || r === "" || r === "home" || r.startsWith("workspace") || r === "app";
//     return esDeskInicial;
//   }
//
//   function hacerRedireccion() {
//     const target = DESTINO[usuarioActual()];
//     if (!target) return;
//     window.__did_user_redirect = true;
//
//     // Pequeña espera para que termine el boot del Desk
//     setTimeout(() => {
//       if (target.startsWith("/")) {
//         // URL absoluto (ej. /app/kiosko-asistencia o ruta externa si procede)
//         window.location.href = target;
//       } else {
//         // Ruta Frappe (ej. "list/Work Order", "form/Item/ITEM-0001", "page/analytics", etc.)
//         frappe.set_route(target);
//       }
//     }, 80);
//   }
//
//   function intentar() {
//     if (debeRedirigir()) hacerRedireccion();
//   }
//
//   // Enganches: al cargar y en el primer cambio de ruta
//   window.addEventListener("load", intentar);
//   if (frappe && frappe.router && frappe.router.on) {
//     frappe.router.on("change", intentar);
//   }
// })();
