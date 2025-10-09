from erpnext.manufacturing.doctype.job_card.job_card import JobCard as _JobCard
import frappe

class JobCard(_JobCard):
    # ===============================================
    # 1) VALIDACIONES RELAJADAS
    # ===============================================
    def validate_job_card(self):
        """Override:
        - NO exigir total_completed_qty == for_quantity
        - NO exigir que Job Cards previos estén en docstatus=1
        Mantén sólo validaciones mínimas de sanidad.
        """
        # (opcional) traza para confirmar que este override corre
        # frappe.log_error("OVERRIDE validate_job_card HIT", "IGCTools Override")

        fq = float(self.for_quantity or 0)
        tc = float(self.total_completed_qty or 0)

        if fq < 0 or tc < 0:
            frappe.throw("Las cantidades no pueden ser negativas.")

        # Si quisieras permitir sólo >= (no menor al plan), descomenta:
        # if tc < fq:
        #     frappe.throw("No puedes someter si lo completado es menor que el plan.")

        # NO LLAMAR al padre: ahí viven las restricciones que queremos quitar.
        # super().validate_job_card()

    # ===============================================
    # 2) (DEFENSIVO) SI EL CORE LLAMA A UN MÉTODO
    #    ESPECÍFICO PARA “PREVIA COMPLETADA”, LO
    #    SOMBREAMOS EN BLANCO PARA NO BLOQUEAR.
    #    (Si el método no existe en tu versión, no afecta.)
    # ===============================================
    def validate_previous_operation_completed(self):
        """Algunas versiones/forks validan que la operación previa esté cerrada/sometida.
        Deja este método en no-op para que NO bloquee por docstatus de previas.
        """
        return

    def validate_previous_job_cards_submitted(self):
        """Alias defensivo (por si el método en tu core tiene otro nombre)."""
        return

    # ===============================================
    # 3) on_submit del padre
    #    (No quitamos on_submit; con la validación relajada,
    #     el flujo estándar de ERPNext continúa sin el throw.)
    # ===============================================
    # def on_submit(self):
    #     return super().on_submit()
