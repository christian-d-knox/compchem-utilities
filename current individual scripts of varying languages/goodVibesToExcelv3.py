spc = input("With SPC correction (Y/N): ")
corrected = False
if (spc == 'y' or "Y"):
    corrected = True

def copy_lines(input_file, output_file, condition):
    try:
        with open(input_file, 'r') as infile:
            lines = infile.readlines()

        filtered_lines = [line for line in lines if condition(line)]

        with open(output_file, 'w') as outfile:
            outfile.writelines(filtered_lines)

        print(f"Filtered lines copied to {output_file} successfully!")
    except FileNotFoundError:
        print(f"Error: File '{input_file}' not found.")

# Example usage: Copy lines containing 'important' from input.txt to output.txt
input_filename = 'Goodvibes_output.dat'
output_filename = 'goodOut.txt'
condition = lambda line: 'o  ' in line.lower()

copy_lines(input_filename, output_filename, condition)

def remove_characters(input_file, output_file, characters_to_remove):
    try:
        with open(input_file, 'r') as infile:
            lines = infile.readlines()

        # Remove specified characters from each line
        modified_lines = [line.replace(characters_to_remove, '') for line in lines]
        
        #Changes Line 1 to be the general headers for later Excel import
        modified_lines[0] = "Structure E ZPE H T.S T.qh-S G(T) qh-G(T)\n"
        
        #Checks for user-specified SPC flag, assumes gas phase else
        if (corrected):
            modified_lines[0] = "Structure E_SPC E ZPE H_SPC T.S T.qh-S G(T)_SPC qh-G(T)_SPC\n"
        
        with open(output_file, 'w') as outfile:
            outfile.writelines(modified_lines)

        print(f"Modified lines written to {output_file} successfully!")
    except FileNotFoundError:
        print(f"Error: File '{input_file}' not found.")

# Example usage: Remove 'xyz' from input.txt and write to output.txt
input_fileN = 'goodOut.txt'
output_fileN = 'goodOut.txt'
characters_to_remove = 'o  '

remove_characters(input_fileN, output_fileN, characters_to_remove)

import pandas as pd

def write_to_excel(input_file, output_excel_file):
    try:
        # Read the content from the input file
        with open(input_file, 'r') as infile:
            lines = infile.readlines()

        # Split each line into columns based on spaces (or any other delimiter)
        data = [line.split() for line in lines]

        # Create a DataFrame from the data
        df = pd.DataFrame(data)

        # Write the DataFrame to an Excel file
        df.to_excel(output_excel_file, index=False, header=False)

        print(f"Data written to {output_excel_file} successfully!")
    except FileNotFoundError:
        print(f"Error: File '{input_file}' not found.")

# Example usage: Write data from input.txt to output.xlsx
inputFile = 'goodOut.txt'
output_excel_filename = 'goodOut.xlsx'

write_to_excel(inputFile, output_excel_filename)
