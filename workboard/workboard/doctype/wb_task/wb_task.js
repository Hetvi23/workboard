// Copyright (c) 2025, Nesscale Solutions Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on('WB Task', {
  refresh(frm) {
    frm.trigger('add_action_buttons');
  },
  add_action_buttons(frm) {
    if (frm.is_new()) return;

    // Re-Open Task - when status is Done or Completed (Red)
    if (['Done', 'Completed'].includes(frm.doc.status)) {
      frm.add_custom_button(__('Re-Open Task'), () => {
        frappe.confirm(
          __('Are you sure you want to re-open this task? Energy points will be reverted.'),
          () => {
            frm.call({
              method: 'reopen',
              doc: frm.doc,
              freeze: true,
              freeze_message: __('Re-opening task...'),
              callback: () => frm.reload_doc()
            });
          }
        );
      });
      frm.change_custom_button_type(__('Re-Open Task'), null, 'danger');
    }

    // Cancel - when status is Open or Overdue (Red)
    if (['Open', 'Overdue'].includes(frm.doc.status)) {
      frm.add_custom_button(__('Cancel'), () => {
        frappe.confirm(
          __('Are you sure you want to cancel this task?'),
          () => {
            frm.call({
              method: 'cancel_task',
              doc: frm.doc,
              freeze: true,
              freeze_message: __('Cancelling task...'),
              callback: () => frm.reload_doc()
            });
          }
        );
      });
      frm.change_custom_button_type(__('Cancel'), null, 'danger');
    }

    const current_user = frappe.session.user;
    const is_assignee = current_user === frm.doc.assign_to;
    const is_assigner = current_user === frm.doc.assign_from;
    const is_admin = current_user === 'Administrator';
    
    // Get WorkBoard Settings including admin role
    frappe.call({
      method: 'workboard.utils.get_workboard_settings',
      callback: (r) => {
        const settings = r.message || {};
        const admin_role = settings.workboard_admin_role;
        const has_admin_role = admin_role && frappe.user_roles.includes(admin_role);
        const only_assignee_can_complete = settings.only_assignee_can_complete;
        
        // Mark Done button - for assignee on Open/Overdue tasks (Manual tasks) - Green
        if (frm.doc.task_type === 'Manual' && ['Open', 'Overdue'].includes(frm.doc.status)) {
          if (is_assignee || is_admin || has_admin_role) {
            frm.add_custom_button(__('Mark Done'), () => {
              frm.call({
                method: 'mark_done',
                doc: frm.doc,
                freeze: true,
                freeze_message: __('Marking as Done...'),
                callback: () => frm.reload_doc()
              });
            });
            frm.change_custom_button_type(__('Mark Done'), null, 'success');
          }
        }
        
        // Mark Completed button - check settings for Manual tasks
        if (frm.doc.task_type === 'Manual' && frm.doc.status === 'Done') {
          let can_mark_complete = false;
          
          if (only_assignee_can_complete) {
            // Only assignee or admin role can mark complete
            can_mark_complete = is_assignee || is_admin || has_admin_role;
          } else {
            // Only assigner or admin role can mark complete (approval workflow)
            can_mark_complete = is_assigner || is_admin || has_admin_role;
          }
          
          if (can_mark_complete) {
            frm.add_custom_button(__('Mark Completed'), () => {
              frm.call({
                method: 'mark_completed',
                doc: frm.doc,
                freeze: true,
                freeze_message: __('Marking as Completed...'),
                callback: () => frm.reload_doc()
              });
            });
            frm.change_custom_button_type(__('Mark Completed'), null, 'success');
          }
        } else if (frm.doc.task_type === 'Auto' && ['Open', 'Overdue'].includes(frm.doc.status)) {
          // For Auto tasks: direct completion from Open/Overdue (Green)
          frm.add_custom_button(__('Mark Completed'), () => {
            frm.call({
              method: 'mark_completed',
              doc: frm.doc,
              freeze: true,
              freeze_message: __('Marking as Completed...'),
              callback: () => frm.reload_doc()
            });
          });
          frm.change_custom_button_type(__('Mark Completed'), null, 'success');
        }
      }
    });
  },
  checklist_template(frm){
    frm.trigger('fetch_checklist');
  },
  fetch_checklist(frm) {
    frm.call({
      method: 'fetch_checklist',
      doc: frm.doc,
      freeze: true,
      callback: (r) => {
        
      }
    });
  }
});

