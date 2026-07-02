import ctypes
import sys
import math 
import os
import re
import time
import numpy as np

from datetime import datetime

def open_galvo(idx_board=0,idx_port=2,CalFn=r".\GM-2020.tsc"):
    gb511_wrap = ctypes.WinDLL(r".\CanonGB511.dll")
    dspfile = 'gbdsp.hex'
    
    #Load galvo functions
    gb511_wrap.ctr_select_board.argtypes = (ctypes.c_ulong, ctypes.c_ulong)
    gb511_wrap.ctr_select_board.restype = ctypes.c_long

    gb511_wrap.ctr_load_program_file.argtypes = (ctypes.c_char_p,)
    gb511_wrap.ctr_load_program_file.restype = ctypes.c_long

    gb511_wrap.ctr_load_correction_file.argtypes = (
        ctypes.c_char_p,
        ctypes.c_double, ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double, ctypes.c_double)
    gb511_wrap.ctr_load_correction_file.restype = ctypes.c_long

    #gb511_wrap.ctr_get_bit_weight.argtypes = (ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_double))
    #gb511_wrap.ctr_get_bit_weight.restype = ctypes.c_long

    gb511_wrap.ctr_read_status.argtypes = (ctypes.POINTER(ctypes.c_ulong),)
    gb511_wrap.ctr_read_status.restype = ctypes.c_long
    
    gb511_wrap.ctr_goto_xy.argtypes = (ctypes.c_long, ctypes.c_long)
    gb511_wrap.ctr_goto_xy.restype = ctypes.c_long
    
    gb511_wrap.ctr_get_current_xy_pos.argtypes = (ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long))
    gb511_wrap.ctr_get_current_xy_pos.restype = ctypes.c_long
    
    
    # load dsp and correction files
    ret = gb511_wrap.ctr_load_program_file(dspfile.encode('cp932'))
    #print(ret)
    gb511_wrap.ctr_load_correction_file(CalFn.encode('cp932'), 1.0, 1.0, 0.0, 0.0, 0.0)
    
    # connect galvo board
    ret=gb511_wrap.ctr_select_board(idx_port,idx_board)
    #print(ret)
    
    # get scanner's unit conversion coefficient [bit/mm]
    #weight=[ctypes.c_double(),ctypes.c_double()]
    #gb511_wrap.ctr_get_bit_weight(0,0,weight[0])
    #gb511_wrap.ctr_get_bit_weight(1,0,weight[1])
    
    # check status
    status = 0
    gb511_wrap.ctr_read_status(ctypes.c_ulong(status))
    
    #return status,weight
    return gb511_wrap, status

def load_cal_galvo(name):       
    recent_file=get_latest_file(name)
    name_recent=f'{name}\{recent_file}'
    
    with open(name_recent,'r') as file:
        #data=file.readlines()
        data=np.loadtxt(file)
        #if ',' in data[0]:
        #    converted_data=[tuple(map(float,item.strip('()\n').split(','))) for item in data]
        #else:
        #    converted_data=[tuple(map(float,item.strip().split())) for item in data]
        #data_Pos,data_Pow=[files[0] for files in converted_data],[files[1] for files in converted_data]
    return data

def get_latest_file(folder):
    files=os.listdir(folder)
    #print(files)
    latest_file=None; latest_date= None

    pattern=r'(\d{6}-\d{4})-galvocal\.txt'
    
    for file in files:
        match=re.search(pattern,file)
        if match:
            date_str=match.group(1)
            date=datetime.strptime(date_str,'%y%m%d-%H%M')
            if latest_date is None or date>latest_date:
                latest_file=file; latest_date=date
    return latest_file


# define Galvo object and methods
class Galvo:
    def __init__(self, CalPn='.\cal_files'):
        self.Xmax=1500 # nm
        self.Ymax=1500 # nm
        self.Pn=CalPn
        
        # load init values
        data=load_cal_galvo(CalPn)
        self.K = data[0] # nm to bit [bit/nm]
        self.X0 = data[1] # nm
        self.Y0 = data[2] # nm
        
        # move back home
        # self.GoHome()

    #def __str__(self):
        #return f"{self.Xmax},{self.X0},{self.pos2bit}"
    
    # relative displacement with respect to home
    def Move(self,DX,DY,gb511_wrap):
        if DX<self.Xmax and DY<self.Ymax: 
            Xb=round(self.Pos2Bit(self.X0+DX)) #bit
            Yb=round(self.Pos2Bit(self.Y0+DY)) #bit
            gb511_wrap.ctr_goto_xy(Xb,Yb)
            #print('Moving by:'+ f'dx1={DX} nm, dx2={DY} nm')
            print('Moving by:'+ f'dx1={Xb-self.Pos2Bit(self.X0)} pulses, dx2={Yb-self.Pos2Bit(self.Y0)} pulses')
        else: 
            print("Targeted position is out of range")
                
    def GoHome(self,gb511_wrap):
        #self.Move(self.X0,self.Y0,gb511_wrap)
        Xb=round(self.Pos2Bit(self.X0)) #bit
        Yb=round(self.Pos2Bit(self.Y0)) #bit
        gb511_wrap.ctr_goto_xy(Xb,Yb)
        print("Moved back home")
        
    def SetCenter(self,DX,DY): # relative displacement of home pos
        self.X0 += DX; self.Y0 += DY        
        print('Center updated')
        
    def SetHomePos(self,DX,DY): # relative displacement of home pos
        self.X0 += DX; self.Y0 += DY        
        # write in cal file
        nowstr = datetime.now().strftime('%y%m%d-%H%M')
        tag='-galvocal.txt';new_file=nowstr+tag
        name_recent=f'{self.Pn}\{new_file}'
        with open(name_recent,'w') as file:
            file.write(f"Calibration [bit/nm]: {self.K}, AX1 home [nm]: {X_new}, AX2 home [nm]: {Y_new}")    
        print('Calibration updated')
        
    def SetHomeBit(self,BX,BY): # change to absolute bit values
        Xnew=self.Bit2Pos(BX); Ynew=self.Bit2Pos(BY)        
        # write in cal file
        nowstr = datetime.now().strftime('%y%m%d-%H%M')
        tag='-galvocal.txt';new_file=nowstr+tag
        name_recent=f'{self.Pn}\{new_file}'
        with open(name_recent,'w') as file:
            file.write(f"Calibration [bit/nm]: {self.K}, AX1 home [nm]: {X_new}, AX2 home [nm]: {Y_new}")    
        print('Calibration updated')        
        
    def SetCal(self,Knew): 
        self.K=Knew        
        # write in cal file
        nowstr = datetime.now().strftime('%y%m%d-%H%M')
        tag='-galvocal.txt';new_file=nowstr+tag
        name_recent=f'{self.Pn}\{new_file}'
        with open(name_recent,'w') as file:
            file.write(f"Calibration [bit/nm]: {Knew}, AX1 home [nm]: {self.X0}, AX2 home [nm]: {self.Y0}")    
        print('Calibration updated')        
        
    def Read(self,gb511_wrap): #read relative position
        read=[ctypes.c_long(),ctypes.c_long()] #bit
        gb511_wrap.ctr_get_current_xy_pos = (read[0], read[1])
        DX=self.Bit2Pos(read[0].value)-self.X0; DY=self.Bit2Pos(read[1].value)-self.Y0;
        return DX,DY
        
    #def Pos2Bit(self,pos,focal=1.5*1e7):
    def Pos2Bit(self,dx): 
        #pulse=int(self.K*pos)
        pulse=round(self.K*dx)
        return pulse
    
    def Bit2Pos(self,bit): 
        x=bit/self.K
        return x
