# PlSource.pm -- pull a named sub (or a marked block) out of
# zmeventnotification.pl so a test can compile and run the real code
# without starting the daemon (the script runs its main loop on load).
package PlSource;
use strict;
use warnings;
use FindBin;

sub source {
    my $file = "$FindBin::Bin/../zmeventnotification.pl";
    open(my $fh, '<', $file) or die "open $file: $!";
    local $/;
    my $src = <$fh>;
    close($fh);
    return $src;
}

# Text from the first line starting with the literal $start up to the end of
# the first later line starting with the literal $end (leading whitespace
# allowed). Dies if either is missing, so a renamed marker fails loudly.
sub extract {
    my ($start, $end) = @_;
    my $src = source();
    $src =~ /^([ \t]*\Q$start\E.*?^[ \t]*\Q$end\E[^\n]*\n)/ms
        or die "PlSource: block $start .. $end not found in zmeventnotification.pl";
    return $1;
}

1;
