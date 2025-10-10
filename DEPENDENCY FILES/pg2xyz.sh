#!/usr/bin/perl
# Houkmol XYZ file Generator.
# Originally Scripted in AWK by Jan Lanbowski.
# Translated to PERL and hacked by Paul Ha-Yeon Cheong.

eval 'exec /usr/bin/perl -S $0 ${1+"$@"}'
    if $running_under_some_shell;

eval '$'.$1.'$2;' while $ARGV[0] =~ /^([A-Za-z_0-9]+=)(.*)/ && shift;

$[ = 1;
$, = ' ';
$\ = "\n";

$j = 0;
$at_symbol{1} = 'H';
$at_symbol{2} = 'He';
$at_symbol{3} = 'Li';
$at_symbol{4} = 'Be';
$at_symbol{5} = 'B';
$at_symbol{6} = 'C';
$at_symbol{7} = 'N';
$at_symbol{8} = 'O';
$at_symbol{9} = 'F';
$at_symbol{10} = 'Ne';
$at_symbol{11} = 'Na';
$at_symbol{12} = 'Mg';
$at_symbol{13} = 'Al';
$at_symbol{14} = 'Si';
$at_symbol{15} = 'P';
$at_symbol{16} = 'S';
$at_symbol{17} = 'Cl';
$at_symbol{18} = 'Ar';
$at_symbol{19} = 'K';
$at_symbol{20} = 'Ca';
$at_symbol{21} = 'Sc';
$at_symbol{22} = 'Ti';
$at_symbol{23} = 'V';
$at_symbol{24} = 'Cr';
$at_symbol{25} = 'Mn';
$at_symbol{26} = 'Fe';
$at_symbol{27} = 'Co';
$at_symbol{28} = 'Ni';
$at_symbol{29} = 'Cu';
$at_symbol{30} = 'Zn';
$at_symbol{31} = 'Ga';
$at_symbol{32} = 'Ge';
$at_symbol{33} = 'As';
$at_symbol{34} = 'Se';
$at_symbol{35} = 'Br';
$at_symbol{36} = 'Kr';
$at_symbol{42} = 'Mo';
$at_symbol{44} = 'Ru';
$at_symbol{45} = 'Rh';
$at_symbol{46} = 'Pd';
$at_symbol{47} = 'Ag';
$at_symbol{48} = 'Cd';
$at_symbol{50} = 'Sn';
$at_symbol{51} = 'Sb';
$at_symbol{53} = 'I';
$at_symbol{54} = 'Xe';
$at_symbol{77} = 'Ir';
$at_symbol{78} = 'Pt';
$at_symbol{79} = 'Au';
$at_symbol{80} = 'Hg';
$at_symbol{81} = 'Tl';
$at_symbol{82} = 'Pb';
$at_symbol{83} = 'Bi';


while (<>) {
    chomp;      # strip record separator
    @Fld = split(' ', $_, 9999);

    if (($Fld[1] eq 'Standard')&($Fld[2] eq 'orientation:')) {
        $_ = &Getline0();
        $_ = &Getline0();
        $_ = &Getline0();
        $_ = &Getline0();
        $_ = &Getline0();
        $i = 0;
        while ($#Fld != 1) {
            $i++;
            $at{$i} = $Fld[2];
            if ($#Fld == 5) {
                $X{$i} = $Fld[3];
                $Y{$i} = $Fld[4];
                $z{$i} = $Fld[5];
            }
            else {
                $X{$i} = $Fld[4];
                $Y{$i} = $Fld[5];
                $z{$i} = $Fld[6];
            }
            $_ = &Getline0();
        }
    }
    if ($Fld[1] eq 'SCF' && $Fld[2] eq 'Done:') {
        $energy = $Fld[5];
    }
    if ($Fld[3] eq 'Threshold') {
        $_ = &Getline0();
        $j++;
    }
}


# print $i;
# print 'Point ', $j, ' Energy= ', $energy;
for ($k = 1; $k <= $i; $k++) {
        print $at_symbol{$at{$k}}, $X{$k}, $Y{$k}, $z{$k};
}

print '';

sub Getline0 {
    if ($getline_ok = (($_ = <>) ne '')) {
        chomp;
        @Fld = split(' ', $_, 9999);
    }
    $_;
}




