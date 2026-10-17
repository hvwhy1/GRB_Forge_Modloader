import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from app_v20 import App


class WheelTests(unittest.TestCase):
 def test_wheel_over_option_scrolls_parent_page_without_changing_value(self):
  root=tk.Tk();root.geometry('500x300+4000+4000')
  try:
   canvas=tk.Canvas(root);canvas.pack(fill='both',expand=True)
   frame=ttk.Frame(canvas);canvas.create_window((0,0),window=frame,anchor='nw')
   value=tk.StringVar(value='Normal');combo=ttk.Combobox(frame,textvariable=value,values=['Vanilla-like','Normal','Hard'],state='readonly');combo.pack()
   canvas.configure(scrollregion=(0,0,500,1800));root.update()
   app=App.__new__(App);app.root=root;app.scrolls={};app.active_page='options'
   root.bind_class('TCombobox','<MouseWheel>',app.scroll_page)
   before=canvas.yview();combo.event_generate('<MouseWheel>',delta=-120);root.update()
   self.assertEqual(value.get(),'Normal');self.assertGreater(canvas.yview()[0],before[0])
  finally:root.destroy()


if __name__=='__main__':unittest.main()
