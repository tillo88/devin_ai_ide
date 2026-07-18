import tkinter as tk
from calculator_logic import Calculator

class CalculatorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title('Calculator')
        self.root.geometry('200x300')

        self.calculator = Calculator()

        self.display = tk.Label(root, text='0', font=('Arial', 40), anchor='e')
        self.display.grid(row=0, column=0, columnspan=4, sticky='e')

        self.unit_label = tk.Label(root, text='Unit: None', font=('Arial', 10))
        self.unit_label.grid(row=1, column=0, columnspan=4, sticky='e')

        self.unit_var = tk.StringVar(value='None')
        self.unit_combo = tk.OptionMenu(root, self.unit_var, 'None', 'm', 'ft', 'kg', 'lb', 's', 'm/s')
        self.unit_combo.grid(row=2, column=0, columnspan=4, sticky='e')

        buttons = [
            ('7', 3, 0), ('8', 3, 1), ('9', 3, 2), ('/', 3, 3),
            ('4', 4, 0), ('5', 4, 1), ('6', 4, 2), ('*', 4, 3),
            ('1', 5, 0), ('2', 5, 1), ('3', 5, 2), ('+', 5, 3),
            ('0', 6, 0), ('.', 6, 1), ('-', 6, 2), ('=', 6, 3),
            ('C', 7, 0), ('CE', 7, 1), ('Convert', 7, 2), ('Add Units', 7, 3)
        ]

        for (text, row, col) in buttons:
            button = tk.Button(root, text=text, font=('Arial', 16), command=lambda t=text: self.on_button_click(t))
            button.grid(row=row, column=col, sticky='e')
            if text == '=':
                button.config(bg='green')
            elif text in '+-*/':
                button.config(bg='yellow')
            elif text in 'CCE':
                button.config(bg='red')
            elif text == 'Convert':
                button.config(bg='lightblue')
            elif text == 'Add Units':
                button.config(bg='lightgreen')

    def on_button_click(self, text):
        if text.isdigit():
            self.calculator.add_digit(int(text))
        elif text == '.':
            self.calculator.add_decimal()
        elif text in '+-*/':
            self.calculator.set_operation(text)
        elif text == '=':
            self.calculator.calculate()
        elif text == 'C':
            self.calculator.clear()
        elif text == 'CE':
            self.calculator.clear_entry()
        elif text == 'Convert':
            target_unit = self.unit_var.get()
            if target_unit != 'None':
                try:
                    self.calculator.convert(target_unit)
                except ValueError as e:
                    self.display.config(text='Error')
        elif text == 'Add Units':
            value = float(self.display_text)
            unit = self.unit_var.get()
            if unit != 'None':
                try:
                    self.calculator.add_with_units(value, unit)
                except ValueError as e:
                    self.display.config(text='Error')
        self.display.config(text=self.calculator.display_text)

if __name__ == '__main__':
    root = tk.Tk()
    app = CalculatorGUI(root)
    root.mainloop()
