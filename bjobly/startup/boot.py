import frappe

def boot_info(bootinfo):
    bootinfo.vite_port = frappe.conf.get("vite_port") or 8090