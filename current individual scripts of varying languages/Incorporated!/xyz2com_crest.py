import os
import numpy as np
import math
import sys

path = './crest_conformers.xyz'

# function for calculating bond length
def calc_length(array,i,j):
    x = np.array(array[i].split()[1:],dtype=np.float32)
    y = np.array(array[j].split()[1:],dtype=np.float32)
    sum_d = 0
    for v in range(len(x)):
        sum_d += (x[v]-y[v])**2
    distance = math.sqrt(sum_d)
    return round(distance,4)

# function for calculating bond angle
def calc_angle(array,i,j,k):
    atomA = np.array(array[i].split()[1:],dtype=np.float32)
    atomB = np.array(array[j].split()[1:],dtype=np.float32)
    atomC = np.array(array[k].split()[1:],dtype=np.float32)
    AB = atomB - atomA
    BC = atomB - atomC
    length_AB = math.sqrt(sum(v**2 for v in AB))
    length_BC = math.sqrt(sum(v**2 for v in BC))
    cos_angle = np.dot(AB,BC)/(length_AB * length_BC)
    angle = math.acos(cos_angle)
    angle = angle * 180 / math.pi
    return round(angle,4)

# function for calculating dihedral
def calc_dihedral(array,i,j,k,m):
    atomA = np.array(array[i].split()[1:],dtype=np.float32)
    atomB = np.array(array[j].split()[1:],dtype=np.float32)
    atomC = np.array(array[k].split()[1:],dtype=np.float32)
    atomD = np.array(array[m].split()[1:],dtype=np.float32)
    AB = atomB - atomA
    BC = atomC - atomB
    CD = atomD - atomC
    n1 = np.cross(AB,BC)
    n2 = np.cross(BC,CD)
    norm_n1 = math.sqrt(sum(v**2 for v in n1))
    norm_n2 = math.sqrt(sum(v**2 for v in n2))
    cos_dihedral = np.dot(n1,n2)/(norm_n1 * norm_n2)
    dihedral = math.acos(cos_dihedral)
    dihedral = dihedral * 180 / math.pi
    
    n3 = np.cross(n1,n2)
    if np.dot(n3,BC) < 0:
        dihedral = -dihedral
    
    return round(dihedral,4)

def main():
    with open(path,'r') as f:
        data = f.readlines()
        line_s = str(data[0]).strip()
        num_atom = int(line_s.strip('\n')) # number of atoms for the molecule
        num_conf = int(len(data)/(num_atom+2)) # number of conformers from crest
        trsd = num_conf # threshold for file conversion

        length = len(sys.argv)
        
        bd_length_asb = []
        angle_asb = []
        dihedral_asb = []
        
        bd_atm1 = 0
        bd_atm2 = 0
        angle_atm1 = 0
        angle_atm2 = 0
        angle_atm3 = 0
        dihedral_atm1 = 0
        dihedral_atm2 = 0
        dihedral_atm3 = 0
        dihedral_atm4 = 0
        
        core = '%nprocshared=12\n'
        mem = '%mem=24GB\n'
        bool_chk = False
        
        inpMB = []
        
        input_filename = 'input.txt'
        input_filepath = os.path.join(os.getcwd(),input_filename)
        
        if os.path.isfile(input_filepath):
            with open(input_filepath,'r') as f5:
                info = f5.readlines()
                inpMB = info
        else:
            inpMB.append('# opt freq m062x 6-31G(d)\n')
            inpMB.append('\n')
            inpMB.append('Title\n')
            inpMB.append('\n')
            inpMB.append('0 1\n') # default setting of method and basis set
            
        if (length > 1):
                for k in range(length):
                    if(sys.argv[k] == '-l'):
                        bd_atm1 = int(sys.argv[k+1])+1
                        bd_atm2 = int(sys.argv[k+2])+1 # read bond length keyword
                    elif(sys.argv[k] == '-a'):
                        angle_atm1 = int(sys.argv[k+1])+1
                        angle_atm2 = int(sys.argv[k+2])+1
                        angle_atm3 = int(sys.argv[k+3])+1 # read bond angle keyword
                    elif(sys.argv[k] == '-d'):
                        dihedral_atm1 = int(sys.argv[k+1])+1
                        dihedral_atm2 = int(sys.argv[k+2])+1
                        dihedral_atm3 = int(sys.argv[k+3])+1
                        dihedral_atm4 = int(sys.argv[k+4])+1 # read dihedral keyword
                    elif(sys.argv[k] == '-n'):
                        trsd = int(sys.argv[k+1]) # read number of conformers converted keyword
                    elif(sys.argv[k] == '-c'):
                        core = '%nprocshared=' + sys.argv[k+1] + '\n' # read core info keyword
                    elif(sys.argv[k] == '-m'):
                        mem = '%mem=' + sys.argv[k+1] + 'GB\n' # read memory keyword
                    elif(sys.argv[k] == '-chk'):
                        bool_chk = True # read .chk file keyword
        
        folder_name = 'crest_conformers'
        if not os.path.exists(folder_name):
            os.mkdir(folder_name)
        else:
            print('Folder ' + folder_name + ' already exists.')
            sys.exit()
        
        for i in range(trsd):
            if(bd_atm1 >=2 and bd_atm2 >=2):
                path_bd_length = './' + folder_name + '/bond_length.txt'
                bd_length_new = calc_length(data,bd_atm1+i*(num_atom+2),bd_atm2+i*(num_atom+2))
                bd_length_asb.append(bd_length_new)
                with open(path_bd_length,'a') as f2:
                    f2.write(str(i+1) + '\t')
                    f2.write(str(bd_length_new))
                    f2.write('\n')
            if(angle_atm1 >=2 and angle_atm2 >=2 and angle_atm3 >=2):
                path_angle = './' + folder_name + '/angle.txt'
                angle_new = calc_angle(data,angle_atm1+i*(num_atom+2),angle_atm2+i*(num_atom+2),angle_atm3+i*(num_atom+2))
                angle_asb.append(angle_new)
                with open(path_angle,'a') as f3:
                    f3.write(str(i+1) + '\t')
                    f3.write(str(angle_new))
                    f3.write('\n')
            if(dihedral_atm1 >=2 and dihedral_atm2 >=2 and dihedral_atm3 >=2 and dihedral_atm4 >=2):
                path_dihedral = './' + folder_name + '/dihedral.txt'
                dihedral_new = calc_dihedral(data,dihedral_atm1+i*(num_atom+2),dihedral_atm2+i*(num_atom+2),dihedral_atm3+i*(num_atom+2),dihedral_atm4+i*(num_atom+2))
                dihedral_asb.append(dihedral_new)
                with open(path_dihedral,'a') as f4:
                    f4.write(str(i+1) + '\t')
                    f4.write(str(dihedral_new))
                    f4.write('\n')
            
            path_save = './' + folder_name + "/crest_conformers_" + str(i+1) + '.com'
                        
            with open(path_save,'w+') as f1:
                f1.write(mem)
                f1.write(core)
                
                if(bool_chk == True):
                    path_save_chk = os.getcwd() + '/' + folder_name + "/crest_conformers_" + str(i+1) + '.chk'
                    f1.write('%chk=' + path_save_chk + '\n')
                    
                if(len(inpMB) <= 5):
                    for k in range(len(inpMB)):
                        f1.write(inpMB[k])
                    for j in range(num_atom):
                        f1.write(data[2+j+i*(num_atom+2)])
                    f1.write('\n')
                elif(len(inpMB) > 5):
                    for k in range(5):
                        f1.write(inpMB[k])
                    for j in range(num_atom):
                        f1.write(data[2+j+i*(num_atom+2)])
                    for m in range(len(inpMB)-5):
                        f1.write(inpMB[m+5])
                    f1.write('\n')


                                
main()
