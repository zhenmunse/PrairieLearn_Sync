import unittest
import os

class TestTypeConversion(unittest.TestCase):
    
    def test_variable_types(self):
        """Test if the student correctly casted the variables."""
        # 1. Check if the student code file is present in docker
        self.assertTrue(os.path.exists("student_code.py"), "File 'student_code.py' was not found.")
        
        # 2. Read the student code from the file
        with open("student_code.py", "r") as f:
            code = f.read()
            
        # 3. Prepare a local environment for executing the student code
        local_env = {
            'price': '15.50', 
            'tip_rate': '20'
        }
        
        # 4. Execute the student code in docker and catch any exceptions
        try:
            exec(code, {}, local_env)
        except Exception as e:
            self.fail(f"Your code raised an error during execution: {e}")
            
        # 5. Use standard unittest assertions for grading
        self.assertIsInstance(
            local_env.get('price'), float, 
            "Error: 'price' was not converted to a float."
        )
        self.assertIsInstance(
            local_env.get('tip_rate'), int, 
            "Error: 'tip_rate' was not converted to an int."
        )