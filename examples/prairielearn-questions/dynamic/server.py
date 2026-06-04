import random

def generate(data):
    # Randomly generate hours (1-4) and minutes (2-59)
    h = random.randint(1, 4)
    m = random.randint(2, 59)
    
    # Calculate total number of minutes
    total_minutes = (h * 60) + m
    
    # Answer string
    expected_statement = f"minutes = {total_minutes}"
    
    # Push variable to frontend HTML (params)
    data["params"]["hours"] = h
    data["params"]["minutes"] = m
    
    # Tell the autograder what correct answer is (correct_answers)
    data["correct_answers"]["student_code"] = expected_statement