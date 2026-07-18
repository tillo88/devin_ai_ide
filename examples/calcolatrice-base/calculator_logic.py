import pint

class Calculator:
    def __init__(self):
        self.current_value = 0
        self.display_text = '0'
        self.operation = None
        self.memory = None
        self.current_unit = None
        self.memory_unit = None
        self.units = pint.UnitRegistry()

    def add_digit(self, digit: int):
        if self.display_text == 'Error':
            self.display_text = '0'
        if self.display_text == '0':
            self.display_text = str(digit)
        else:
            self.display_text += str(digit)

    def add_decimal(self):
        if '.' not in self.display_text:
            self.display_text += '.'

    def set_operation(self, op: str):
        if self.display_text != '0':
            self.operation = op
            self.memory = float(self.display_text)
            self.memory_unit = self.current_unit
            self.display_text = '0'

    def calculate(self):
        if self.operation is not None:
            current = float(self.display_text)
            if self.operation == '+':
                result = self.memory + current
            elif self.operation == '-':
                result = self.memory - current
            elif self.operation == '*':
                result = self.memory * current
            elif self.operation == '/':
                if current != 0:
                    result = self.memory / current
                else:
                    result = 'Error'
            self.current_value = result
            self.display_text = str(result)
            self.operation = None
            self.memory = None
            self.memory_unit = None
            return result
        return None

    def clear(self):
        self.current_value = 0
        self.display_text = '0'
        self.operation = None
        self.memory = None
        self.current_unit = None
        self.memory_unit = None

    def clear_entry(self):
        self.display_text = '0'

    def convert(self, target_unit: str) -> float:
        if self.current_unit is None:
            raise ValueError("No unit set for conversion")
        converted = self.units.convert(self.current_value, self.current_unit, target_unit)
        self.current_value = float(converted)
        self.current_unit = target_unit
        self.display_text = str(self.current_value)
        return self.current_value

    def add_with_units(self, value: float, unit: str) -> float:
        if self.current_unit is None:
            raise ValueError("No current unit set")
        if unit is None:
            raise ValueError("No unit provided for new value")
        try:
            self.units.check_dimensionality(self.current_unit, unit)
        except pint.DimensionalityError:
            raise ValueError("Incompatible units cannot be added")
        converted_value = self.units.convert(value, unit, self.current_unit)
        result = self.memory + converted_value if self.memory is not None else converted_value
        self.current_value = result
        self.display_text = str(result)
        self.current_unit = self.current_unit
        return result
