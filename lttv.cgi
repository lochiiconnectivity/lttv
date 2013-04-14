#!/usr/bin/perl
#
#LTTV - davef - NOT GPL thankyouverymuch 
#
#Please ensure you have the following pieces of software installed to use lttv,
#here are the debian/ubuntu package names:
#
#graphviz
#libdeps-renderer-dot-perl
#libgd-gd2-perl
#libgd-text-perl
#libgd2-(no)xpm
#libgraphviz-perl
#libgraphviz4
#libgv-perl
#msttcorefonts
#

#use lib qw(/usr/lib64/graphviz/perl);
#use lib qw(.);
use GD 2.35;
use GD::Text 0.86;
use CGI qw(:standard);
use CGI::Carp qw (fatalsToBrowser);
use Data::Dumper;
use GraphViz;
use Bit::Vector;
use Net::Telnet::Cisco;
use Storable;
use strict;

my $VERSION = "1.3p";

#Replace this with authentication details for your routers
#Telnet will be used (See Net::Telnet::Cisco)
my $username = "username";
my $password = "password";

my $arialfont = '/usr/share/fonts/truetype/msttcorefonts/arial.ttf';
die ("You must have the MS Truetype Core Fonts (msttcorefonts) installed to use LTTV") unless ( -e $arialfont );


my %nodes;		#Main node db
my %nodedata;		#Cache for the parrellisation
my %nodeattrs;		#Additional attributes of nodes
my %linkassigns;	#Main linkassigns db
my %mstmap;		#MST Maps

my $linebreak = 7;	#Line break at 15 items

my %layouts = (
			'circo'	=>	'circo',
			'neato'	=>	'neato',
			'fdp'	=>	'fdp',
			'twopi'	=>	'twopi',
); my @l = (keys %layouts); my $layoutmenu = \@l;

#Please replace these with your devices
my %clusters = (
                        'dc1switches'	=>	['switch1','switch2'],
); my @c = (keys %clusters); @c = sort(@c); my $clustermenu = \@c;

my $layout = $layouts{param('layout')} || 'circo';
my $portchannels = param('portchannels') || 'compress';
my $cluster = param('cluster');
my $nodelist = param('nodelist');
my $debug = param('debug');
my $displayvlan = param('vlan');
my $image = param('image');
my @nodelist;

if ($debug || $image) {

	if (($nodelist) && ($nodelist=~m/\|/)) {
		@nodelist = split (/\|/,$nodelist);
	}
	elsif ($cluster) {
		@nodelist = @{$clusters{$cluster}};
	}
	else {
		die "nodelist or cluster not provided";
	}

	&parsenodelist(@nodelist);
}
	

#User initiated light debug, only dumps the nodedb, for linkassign debugging, see later on
if ($debug == 1) {
	&printmenu;
	print "<pre>\n";
	print Dumper(%nodeattrs);
	print "<\/pre>\n";
	exit;
}
elsif ($image) {
	if ($image == 1) {
		my $querystring = $ENV{'HTTP_REFERER'};
		$querystring .= "?image=2&";
		my @varnames = CGI::param();
		my @vars;
		foreach my $varname (@varnames) {
			my $varstring = $varname . "=" . param($varname);
			push (@vars,$varstring) unless ($varname eq 'image');
		}
		$querystring .= join('&',@vars);
		&printmenu;
		print "<img src=\"$querystring\">\n";
		exit;
	}
	elsif ($image == 2) {
		&rendergraph;
		exit;
	}
	else {
		die "Unknown image mode";
	}

}
else {
	&printmenu;
	exit;
}

sub getdat ($) {
	
	my $node = shift;

	return unless ($node);

	my (@verarr,@cdparr,@ssbarr,@rparr,@vlanarr,@pcarr,@vlansarr,@mstarr);

	my $ntc = Net::Telnet::Cisco->new(
				Host	=>	$node,
				#Dump_Log=>	"/tmp/dl-$node-$$.dl",			# For debugging only
	);
	$ntc->login($username,$password) || die "Could not login to $node";
	$ntc->enable($password) || die "Could not enable on $node";
	#Optional Commands
	eval {
		$ntc->cmd('term len 0');
	};
	@verarr = $ntc->cmd('show ver');
	@cdparr = $ntc->cmd('show cdp neighbors');
	@ssbarr = $ntc->cmd('show spanning blockedport');
	@rparr = $ntc->cmd('show spanning-tree root');
	@vlanarr= $ntc->cmd('sh int trunk | exc trunking|Port');
	@pcarr  = $ntc->cmd('sh etherchannel summary | begin Group');
	#Optional Commands
	eval {
		@vlansarr= $ntc->cmd('sh int switchport | in Name|Administrative Mode|Access Mode VLAN');
		@mstarr = $ntc->cmd('show spanning-tree mst configuration');
	};

	return (\@verarr,\@cdparr,\@ssbarr,\@rparr,\@vlanarr,\@pcarr,\@vlansarr,\@mstarr);

}

sub setdata ($) {
	my $node = shift;
	return unless($node);
	my $file = "/tmp/$node.ltv";
	unlink($file) if ($file);
	my @data = getdat($node);
	store (\@data, $file) || die ("Can not store data in file $file $!");	
	return;
}

sub getdata ($) {
	my $node = shift;
	return unless($node);
	my $file = "/tmp/$node.ltv";
	if ( -e $file ) {
		my $data = retrieve($file);
		unlink($file);
		return (@{$data});
	}
	return;
}


sub printmenu() {

        print 	header(-type=>'text/html',-expires=>'now',-cache_control=>'no-cache, no-store, must-revalidate'), 
		start_html('LTTV'),
		h3('LTTV'),
		CGI::start_form(),
		CGI::start_table(),
		CGI::start_Tr(),
		th('Debug'),
		th('Cluster'),
		th('Layout'),
		th('PortChannels'),
		th('Vlan'),
		th('Render'),
		CGI::end_Tr(),
		CGI::start_Tr(),
		td(popup_menu(-name=>'debug',-values=> [0,1])),
		td(popup_menu(-name=>'cluster',-values=> $clustermenu)),
		td(popup_menu(-name=>'layout',-values=> $layoutmenu)),
		td(popup_menu(-name=>'portchannels',-values=> ['compress','expand'])),
		td(textfield('vlan')),
		td(submit),
		CGI::end_Tr(),
		CGI::end_table(),
		hidden(-name=>'image',-value=>1),
                CGI::end_form();

	return;
}

sub parsenodelist (@) {

	my @nodelist = @_;

	#Parrellisation, fork a child to call setdata for each node
	foreach my $node (@nodelist) {
		my $pid = fork();
		if (not defined $pid) {
			die "Can not fork! $!";
		}
		elsif ($pid == 0) {
			setdata($node);
			exit(0);
		}
	}

	1 while (wait() != -1);		# Wait for all children to finish

	foreach my $node (@nodelist) {

		my ($verarr,$cdparr,$ssbarr,$rparr,$vlanarr,$pcarr,$vlansarr,$mstarr) = getdata($node);

		$node=lc($node);
		$node=~s/\..*//g;

		{	#Parse Version
			my $ver;
			foreach (@{$verarr}) {
				if ($_=~m/^[\n]*cisco (\S+) /) {
					$ver = $1;
				}
			}
			$nodeattrs{$node}{'model'} = $ver;
		}

		{	#Parse CDP	
			my ($hn);
			foreach (@{$cdparr}) {
				chomp;
				if ($_=~m/^[\n]*(\S+)\s+(\S+) (\S+)\s+\d+\s+(.*)$/) {	#Whole line
					my $hn = $1;
					my $int = "$2 $3";
					my $linkpartner = $4;
					next unless ($int=~m/\/|\d+/);
					$hn=lc($hn);
					$hn=~s/\..*//g;
					$int = normal($int);
					$linkpartner=~s/.* (\S+) (\S+)/$1 $2/g;
					$linkpartner=normal($linkpartner);
					push(@{$nodes{$node}{$int}{'cdp'}},{$hn => $linkpartner});
				}
				elsif ($_=~m/Device ID/) {				#Paging Rubbish
					next;
				}
				elsif ($_=~m/^[\n]*(\S+)/) {					#Just HN
					$hn = $1;
					$hn=lc($hn);
					$hn=~s/\..*//g;
				}
				elsif ($_=~m/^[\n]*\s+(\S+) (\S+)\s+\d+\s+(.*)/) {		#Just dets
					my $int = "$1 $2";
					my $linkpartner = $3;
					next unless ($int=~m/\/|\d+/);
					$int = normal($int);
					$linkpartner=~s/.* (\S+) (\S+)/$1 $2/g;
					$linkpartner=normal($linkpartner);
					push(@{$nodes{$node}{$int}{'cdp'}},{$hn => $linkpartner});
				}
			}
		}

		{	#Parse sh vlan all

			foreach (@{$vlanarr}) {
				chomp;
				if ($_=~m/^[\n]*(\S+)\s+([\d\-\,]+)$/) {
					my $int = $1;
					my $vlans = $2;
					$int = normal($int);
					my @varr = split (/,/,$vlans);
					foreach my $vlan (@varr) {
						if ($vlan=~m/(\d+)-(\d+)/) {	#need to expand
							my $svlan = $1; my $evlan = $2;
							if ($svlan == 1 and $evlan == 4094) {
								$nodes{$node}{$int}{'vlan'}{'all'} = 'all';
							}
							else {
								my $i;
								for ($i=$svlan;$i<=$evlan;$i++) {
									if (($displayvlan && $displayvlan == $i) || (!$displayvlan)) {
										$nodes{$node}{$int}{'vlan'}{$i} = $i;
									}
								}
							}
						}
						elsif ($vlan=~m/(\d+)/) {
							if (($displayvlan && $displayvlan == $vlan) || (!$displayvlan)) {
								$nodes{$node}{$int}{'vlan'}{$vlan} = $vlan;
							}
						}
					}
					if ($nodes{$node}{$int}{'vlan'}{'all'}) {
						delete $nodes{$node}{$int}{'vlan'};
						$nodes{$node}{$int}{'vlan'}{'all'} = 'all';
					}
				}
			}
					
		}
		{	#Parse sh int switchport to catch access vlans (if we have it)
			my $int;
			VSARR:
			foreach (@{$vlansarr}) {
				chomp;
				if ($_=~m/^[\n*]Name: (\S+)/) {
					$int = $1;
					$int = normal($int);
				}
				elsif ($int && $_=~m/^[\n]*Administrative Mode: (.*)/) {
					if ($1!~m/access/) {
						undef($int);
						next VSARR;
					}
				}
				elsif ($int && $_=~m/^[\n]*Access Mode VLAN: (\d+)/) {
					$nodes{$node}{$int}{'vlan'}{$1} = $1;
					undef($int);
					next VSARR;
				}
				
			}
		}
		{	#Parse blocked list

			foreach (@{$ssbarr}) {

				chomp;

				if ($_=~m/^[\n]*VLAN(\d+)\s+(.*)$/) {
					my $vlan = $1;
					$vlan=int($vlan);
					my $ports = $2;
					next unless ($ports=~m/\//);	#No unpopped vlans
					my @parr = split (/, /,$ports);
					foreach my $int (@parr) {
						$int = normal($int);
						if (($displayvlan && $displayvlan == $vlan) || (!$displayvlan)) {
							$nodes{$node}{$int}{'blockvlan'}{$vlan} = $vlan;
						}
					}
				}
			}

		}
		{	#Parse root list
			
			foreach (@{$rparr}) {
	
				chomp;

				if ($_=~m/^[\n]*VLAN(\d+)\s+\d+\s+\S+\s+\d+\s+\d+\s+\d+\s+\d+\s(.*)$/) {
					my $vlan = $1;
					$vlan=int($vlan);
					my $ports = $2;
					next unless ($ports=~m/\//);	#No unpopped vlans
					my @parr = split (/, /,$ports);
					foreach my $int (@parr) {
						$int = normal($int);
						if (($displayvlan && $displayvlan == $vlan) || (!$displayvlan)) {
							$nodes{$node}{$int}{'rootvlan'}{$vlan} = $vlan;
						}
					}
				}
			}
		}
		{	#Parse etherchannel summary

			my ($pc);
			foreach (@{$pcarr}) {
				chomp;
				
				if ($_=~m/^[\n]*(\d+)\s+Po(\d+)\(\S+\)\s+\S*\s+(.*)/) {
					$pc = "PortChannel$2";
					my $ifstr = $3;
					my @ifarr = split (/ /,$ifstr);
					foreach my $int (@ifarr) {
						if ($int=~m/(\S+)\(\S+\)/) {
							$int=normal($1);
							if ($portchannels eq 'expand') {	# If in expanded mode, take portchannels vlans and place them under the int
								foreach my $vlan (sort keys %{$nodes{$node}{$pc}{'vlan'}}) {
									$nodes{$node}{$int}{'vlan'}{$vlan} = $vlan;
								}
								foreach my $vlan (sort keys %{$nodes{$node}{$pc}{'blockvlan'}}) {
									$nodes{$node}{$int}{'blockvlan'}{$vlan} = $vlan;
								}
								foreach my $vlan (sort keys %{$nodes{$node}{$pc}{'rootvlan'}}) {
									$nodes{$node}{$int}{'rootvlan'}{$vlan} = $vlan;
								}
							}
							else {					# Else steal the CDP neighbor and record
								foreach my $cdpneighbor (@{$nodes{$node}{$int}{'cdp'}}) {
									push(@{$nodes{$node}{$pc}{'cdp'}}, $cdpneighbor);
									$nodes{$node}{$int}{'stolenby'} = $pc;
								}
							}
						}
					}
				}
				elsif ($_=~m/^[\n]*\s+(.*)/) {                 #Just INT
					my $ifstr = $1;
					my @ifarr = split (/ /,$ifstr);
					foreach my $int (@ifarr) {
						if ($int=~m/(\S+)\(\S+\)/) {
							$int=normal($1);
							if ($portchannels eq 'expand') {	# If in expanded mode, take portchannels vlans and place them under the int
								foreach my $vlan (sort keys %{$nodes{$node}{$pc}{'vlan'}}) {
									$nodes{$node}{$int}{'vlan'}{$vlan} = $vlan;
								}
								foreach my $vlan (sort keys %{$nodes{$node}{$pc}{'blockvlan'}}) {
									$nodes{$node}{$int}{'blockvlan'}{$vlan} = $vlan;
								}
								foreach my $vlan (sort keys %{$nodes{$node}{$pc}{'rootvlan'}}) {
									$nodes{$node}{$int}{'rootvlan'}{$vlan} = $vlan;
								}
							}
							else {					# Else steal the CDP neighbor and record
								foreach my $cdpneighbor (@{$nodes{$node}{$int}{'cdp'}}) {
									push(@{$nodes{$node}{$pc}{'cdp'}}, $cdpneighbor);
									$nodes{$node}{$int}{'stolenby'} = $pc;
								}
							}
						}
							
					}
				}
			}
		}
		{	# Parse MST
			my $mstregion;
                        foreach (@{$mstarr}) {
                                chomp;
				if ($_=~m/^Name\s+\[(\S+)\]$/) {
					$mstregion = $1;
				}
				elsif ($_=~m/^(\d+)\s+(\S+)$/) {
					$nodes{$node}{'mst'}{$mstregion} = 1;
					$mstmap{$mstregion}{$1} = $2;
				}
			}
		}
	}

	{	# Reconcile any differences created by portchannel expansion or compression
		if ($portchannels eq 'expand') {	# In expansion mode, clean up all the portchannel ints
			foreach my $node (sort keys %nodes) {
				foreach my $int (keys %{$nodes{$node}}) {
					delete $nodes{$node}{$int} if ($int=~m/PortChannel/);
				}
			}
		}
		else {					# In compression mode, clean up all the non portchannel ints 

			# first clear up the CDP neighbors we stole
			# This means going through everybody elses neighbors and looking
			# For links back to the portchannel interfaces you stole for

			foreach my $node (sort keys %nodes) {
				foreach my $int (sort keys %{$nodes{$node}}) {
					next if ($int=~m/PortChannel/);
					next unless $nodes{$node}{$int}{'cdp'};
   						foreach my $cdplinkage (@{$nodes{$node}{$int}{'cdp'}}) {
                                        		foreach my $cdpnode ( keys %{$cdplinkage}) {
							my $cdpnodeint = $cdplinkage->{$cdpnode};
							if ($nodes{$cdpnode}) {			# Ensure we dont autovivify nodes
								if ($nodes{$cdpnode}{$cdpnodeint}) {
									my $stolenby = $nodes{$cdpnode}{$cdpnodeint}{'stolenby'};
									if ($stolenby) {
										$cdplinkage->{$cdpnode} = $stolenby;
									}
								}
							}
						}
					}
				}
			}

			# Next go back and delete the ints stolen from
			foreach my $node (sort keys %nodes) {
				foreach my $int (sort keys %{$nodes{$node}}) {
					next if ($int=~m/PortChannel/);
					delete $nodes{$node}{$int} if ($nodes{$node}{$int}{'stolenby'});
				}
			}

			# Finally, dedupe any CDP relationships by rewriting them for portchannels
			foreach my $node (sort keys %nodes) {
				foreach my $int (sort keys %{$nodes{$node}}) {
					next unless ($int=~m/PortChannel/);
					next unless $nodes{$node}{$int}{'cdp'};
					my ($cdpnode, $cdpnodeint);
					DDINT:
					foreach my $cdplinkage (@{$nodes{$node}{$int}{'cdp'}}) {
                                        	foreach my $lcdpnode ( keys %{$cdplinkage} ) {
							$cdpnode = $lcdpnode;
							$cdpnodeint = $cdplinkage->{$cdpnode};
							last DDINT;
						}
					}
					$nodes{$node}{$int}{'cdp'} = [ { $cdpnode => $cdpnodeint } ];
				}
			}

		}
	}

	{	# Now deal with any unknown nodes

                        foreach my $node (sort keys %nodes) {
                                foreach my $int (sort keys %{$nodes{$node}}) {
                                        next unless $nodes{$node}{$int}{'cdp'};
                                        foreach my $cdplinkage (@{$nodes{$node}{$int}{'cdp'}}) {
                                        	foreach my $cdpnode ( keys %{$cdplinkage}) {
							unless (defined($nodes{$cdpnode})) {
								#Stamping an ent in nodeattrs is best way to make our node known
								#Use the model attr since we can make out an error in the node label
								$nodes{$cdpnode} = {};
								$nodeattrs{$cdpnode}{'model'} = '?';
							}
							
						}
					}
				}
			}
	}


	{	# Set the node attributes based on what we have learnt, get a list of nodes which are defined or not

		foreach my $node (sort keys %nodeattrs) {
			unless ($nodeattrs{$node}{'model'} =~ m/CBS/) {		# Blade switches must be relegated
				$nodeattrs{$node}{'paint'}{'rank'} = 'top';
				$nodeattrs{$node}{'paint'}{'width'}=2;
				$nodeattrs{$node}{'paint'}{'height'}=2;
			}
			$nodeattrs{$node}{'paint'}{'label'}="$node\n$nodeattrs{$node}{'model'}";
			$nodeattrs{$node}{'paint'}{'label'}.="\nMST REGION " . ~~(keys %{$nodes{$node}{'mst'}})[0] if ($nodes{$node}{'mst'});	# Paint MST Region on the node
			$nodeattrs{$node}{'node'}=$nodes{$node};
		}
	}

	{	# Finally, delete any unconnected nodes
	}

}

sub normal {	#Normalise int names

	my $inint = shift;

	return unless ($inint);

	#Remove crap from buggy switches
	$inint=~s/\s+//g;
	$inint=~s/(.*)-(.*)$/$2/g;

	$inint=~s/^Po([\s|\d|\.])/PortChannel$1/g;
	$inint=~s/^Fas([\s|\d|\.])/FastEthernet$1/g;
	$inint=~s/^Fa([\s|\d|\.])/FastEthernet$1/g;
	$inint=~s/^Gig([\s|\d|\.])/GigabitEthernet$1/g;
	$inint=~s/^Gi([\s|\d|\.])/GigabitEthernet$1/g;
	$inint=~s/^G([\s|\d|\.])/GigabitEthernet$1/g;
	$inint=~s/^Ten([\s|\d|\.])/TenGigabitEthernet$1/g;
	$inint=~s/^Te([\s|\d|\.])/TenGigabitEthernet$1/g;
	$inint=~s/^T([\s|\d|\.])/TenGigabitEthernet$1/g;

	#Remove crap from buggy switches
	$inint=~s/(.*)-(.*)$/$2/g;

	chomp($inint);

	return ($inint);

}

sub abnormal {	#Shorten interface names!

	my $inint = shift;

	return unless ($inint);
	$inint=~s/^(\w)([a-zA-Z]+)(\d+)\/(\d+)\/([\d|\.]+)$/$1$3\/$4\/$5/g;
	$inint=~s/^(\w)([a-zA-Z]+)(\d+)\/([\d|\.]+)$/$1$3\/$4/g;
	$inint=~s/^(\w)([a-zA-Z]+)(\d+)$/$1$3/g;

	return ($inint);

}

sub rendergraph {	#Render the graph

	my $g = GraphViz->new(
				directed	=>	1,			#Graph is undirected
				layout		=>	$layout,		#Circo layout (fdp,circo,twopi,neato)
				concentrate	=>	0,			#No edge merging
				random_start	=>	1,			#No random start
				epsilon		=>	0.1,			#Graph speed vs quality (inversely proportional to quality)
				rankdir		=>	0,			#Ranking direction (0=updown, 1=leftright)
#				width		=>	20,			#Width
#				height		=>	20,			#Height
				overlap		=>	'scale',		#Rescale if overlaps occur
				#ratio		=>	'fill',			#Fill the page
				node 		=> 	{shape => 'box'},	#All nodes should be boxes
	);
	foreach my $node (sort keys %nodes) {	#Add nodes must be added
		INT:
		foreach my $int (sort keys %{$nodes{$node}}) {
			if ($nodes{$node}{$int}{'cdp'}) { 						# Only create nodes with cdp
				if (	#DisplayVLAN Filter
					(($nodes{$node}{$int}{'vlan'}{$displayvlan}) || ($nodes{$node}{$int}{'vlan'}{'all'})) || 
					(!$displayvlan)
				) {
					unless ($nodeattrs{$node}{'added'}) {				#Filter node addition if node has been created already

						$g->add_node($node,
									rank   => $nodeattrs{$node}{'paint'}{'rank'},
									width  => $nodeattrs{$node}{'paint'}{'width'},
									height => $nodeattrs{$node}{'paint'}{'height'},
									label  => $nodeattrs{$node}{'paint'}{'label'},
						);
						$nodeattrs{$node}{'added'} = 1;
					}
				}
				foreach my $cdplinkage (@{$nodes{$node}{$int}{'cdp'}}) {
					foreach my $cdpnode ( keys %{$cdplinkage }) {
						my $cdpnodeint = $cdplinkage->{$cdpnode};
						#Normalise
						$cdpnode=lc($cdpnode);
						#Remove domain names
						$cdpnode=~s/\..*//g;
						#Remove "Switch"
						if ($cdpnode eq 'Switch') {
							my $rand = int(rand(10));
							$cdpnode .= "_$rand";
						}
						next if ($node eq $cdpnode);	#Don't add links to yourself DONT MOVE THIS STATEMENT

						#Finally, if the destination node does not exist, please add it
						if (	#DisplayVLAN Filter
							(($nodes{$node}{$int}{'vlan'}{$displayvlan}) || ($nodes{$node}{$int}{'vlan'}{'all'})) || 
							(!$displayvlan)
						) {
							unless ($nodeattrs{$cdpnode}{'added'}) {			#Filter node addition if node has been created already
								$g->add_node($cdpnode,
											rank   => $nodeattrs{$cdpnode}{'paint'}{'rank'},
											width  => $nodeattrs{$cdpnode}{'paint'}{'width'},
											height => $nodeattrs{$cdpnode}{'paint'}{'height'},
											label  => $nodeattrs{$cdpnode}{'paint'}{'label'},
								);
								$nodeattrs{$cdpnode}{'added'} = 1;
							}
						}

						#Now populate linkassigns
						#Before we do anything, check for a backlink , if we find it skip since it will duplicate our effort
						#next if ($linkassigns{$cdpnode}{$cdpnodeint}{$node}{$int});
						#Now add links for each vlan
						foreach my $vlan (sort keys %{$nodes{$node}{$int}{'vlan'}}) {
							next if ($displayvlan && (($vlan ne $displayvlan) && ($vlan ne 'all')));	# Dont produce links for anything outside filter if present
							my $blockstate = defined($nodes{$node}{$int}{'blockvlan'}{$vlan}) ? 'block' : 'noblock';
							my $backblockstate = defined($nodes{$cdpnode}{$cdpnodeint}{$node}{$int}{'blockvlan'}{$vlan}) ? 'block' : 'noblock';
							my $rootstate = defined($nodes{$node}{$int}{'rootvlan'}{$vlan}) ? 'root' : 'noroot';
							my $backrootstate = defined($nodes{$cdpnode}{$cdpnodeint}{$node}{$int}{'rootvlan'}{$vlan}) ? 'root' : 'noroot';
							if ($linkassigns{$cdpnode}{$cdpnodeint}{$node}{$int}) {	#Is there a backlink?
								if ($rootstate eq 'root' || $blockstate eq 'block') {	#Are we root or do we block our vlan?
									push (@{$linkassigns{$node}{$int}{$cdpnode}{$cdpnodeint}{$rootstate}{$blockstate}}, $vlan); # Use it
								}
								elsif ($backrootstate eq 'root' || $backblockstate eq 'block') {	#Same for backlink?
									push (@{$linkassigns{$node}{$int}{$cdpnode}{$cdpnodeint}{$backrootstate}{$backblockstate}}, $vlan); # Use it
								}
								else { #Ignore it
									next INT;
								}
								#Reconcile the root/noroot state. 
								delete $linkassigns{$cdpnode}{$cdpnodeint}{$node}{$int} if (
									(
										$linkassigns{$node}{$int}{$cdpnode}{$cdpnodeint}{'root'}{'block'} && 
										$linkassigns{$cdpnode}{$cdpnodeint}{$node}{$int}{'noroot'}{'noblock'}
									) ||
									(
										$linkassigns{$node}{$int}{$cdpnode}{$cdpnodeint}{'root'}{'noblock'} && 
										$linkassigns{$cdpnode}{$cdpnodeint}{$node}{$int}{'noroot'}{'noblock'}
									) ||
									(
										$linkassigns{$node}{$int}{$cdpnode}{$cdpnodeint}{'noroot'}{'block'} && 
										$linkassigns{$cdpnode}{$cdpnodeint}{$node}{$int}{'noroot'}{'noblock'}
									) 
								) ;

							}
							else {
								push (@{$linkassigns{$node}{$int}{$cdpnode}{$cdpnodeint}{$rootstate}{$blockstate}},	$vlan);	#Make a frontlink
							}
						}
					}
				}
			}
		}
	}
	#
	#Various debugging stuffs, if you want to inspect linkassigns in the interface, uncomment next line
	#
	#print "content-type:	text/html\n\n\n<pre>" . Dumper(%linkassigns);exit;
	#
	#However, if you want to inspect linkassigns in a logfile, uncomment next four lines
	#
	#open (DLOG,">> /tmp/linkassigns.log");
	#print DLOG "\n------------\nNEW LINKASSIGN\n-------------\n";
	#print DLOG Dumper(%linkassigns);
	#close DLOG;
	{
	#Now build links out of the linkassigns
	foreach my $node (sort keys %linkassigns) {
		foreach my $int (sort keys %{$linkassigns{$node}}) {
			foreach my $cdpnode (sort keys %{$linkassigns{$node}{$int}}) {
				foreach my $cdpnodeint (sort keys %{$linkassigns{$node}{$int}{$cdpnode}}) {
					foreach my $rootstate (sort keys %{$linkassigns{$node}{$int}{$cdpnode}{$cdpnodeint}}) {
					foreach my $blockstate (sort keys %{$linkassigns{$node}{$int}{$cdpnode}{$cdpnodeint}{$rootstate}}) {
	
						next unless ($cdpnodeint=~m/\/|\d+/);	#No bogus dest ints
						my @vlans;
						my @invlans = @{$linkassigns{$node}{$int}{$cdpnode}{$cdpnodeint}{$rootstate}{$blockstate}};
						my @ranges = rangify(@invlans);	#Summarise
						my $vlanctr = 1;
						my $minlen = 1;
	    					foreach my $range (@ranges) {
							my ($start, $end) = ($range->[1], $range->[2]); 
							if ($start && $end) {
								if ($start == $end) {
									push (@vlans,$start);
								}
								else {
									push (@vlans,"$start-$end");
								}
							}
							elsif ($start eq 'all') {
								push (@vlans,"all");
							}
							elsif ($start eq 'err') {		# Something odd happened
								push (@vlans,"err");
							}
							#linebreak the list
							if (($vlanctr / $linebreak) == int ($vlanctr / $linebreak)) {
								push (@vlans,"\n");
								$minlen += 35;
							}
							$vlanctr++;
	    					}
						my $vlanstr = join(',',@vlans);

						my $headlabel = abnormal($int)		||	'unknown';
						my $taillabel = abnormal($cdpnodeint)	||	'unknown';

						my $arrowhead = 'none';
						if ($rootstate eq 'root') {
							if ($blockstate eq 'block') {
								 $arrowhead = 'invdot';
							}
							else {
								$arrowhead = 'inv';
							}
						}
						else {
							if ($blockstate eq 'block') {
								$arrowhead = 'dot';
							}
						}
	
						$g->add_edge(
							$node           =>      $cdpnode,               	#Node is related to CDPNode
							minlen		=>	$minlen,
							taillabel	=>	$headlabel,			#BUG!
							headlabel	=>	$taillabel,			#BUG!
							arrowhead	=>	$arrowhead,
							arrowtail	=>	'none',
							fontsize	=>	6,
							label		=>	$vlanstr,			#Int data
							labelangle	=>	180,
						);
					}
					}
				}
			}
		}
	}
	}
	
#Produce graph
	#
	#Debug graph by enabling the next few lines to expose the DOT
	#print "Content-Type:	text/plain\n\n<pre>\n";
	#print $g->as_dot;
	#exit;
	#Put it into GD Object
	my $ingraphgd = GD::Image->newFromGdData($g->as_gd);

	#Get data about this new object
	my ($inwidth,$inheight) = $ingraphgd->getBounds();

	#exit if there are no image bounds;
	exit unless ($inwidth && $inheight);

	#Copy this data into a new image, which is bigger than the old one
	my $width = $inwidth;
	$width+=100 if ($width <= 660);
	my $height = $inheight + 300;
	my $graphgd = GD::Image->new($width,$height,1);

	#Turn on alpha mode
	$graphgd->alphaBlending(1);

	#Now fill the defecit with white
	$graphgd->fill(0,0,0x00FFFFFF);

	#Calclate center of diagram
	my $centerx = int(($width/2));
	my $centery = int(($height/2));

	#Turn on alpha mode
	$graphgd->alphaBlending(1);

	#Add copyright
	my $date = `date`;
	my $cstring1 = "Visualisation of cluster $cluster";
	$cstring1.= " (vlan $displayvlan)" if ($displayvlan);
	$cstring1.= "\r\non date $date";
	$graphgd->stringFT(0x00000000,$arialfont,14,0,0,50,$cstring1);
	my $cstring2= "COMPANY CONFIDENTIAL AND PROPRIATORY";
	#my $cstring2= "$inwidth,$inheight";
	$graphgd->stringFT(0x00FF0000,$arialfont,14,0,0,90,$cstring2);

	#Add legend
	#Show root icon (an arrow) , describe it as "Port is root port" with a string

	my $rootarrow = new GD::Polygon;
	$rootarrow->addPt(500,30);
	$rootarrow->addPt(500,40);
	$rootarrow->addPt(520,35);
	$graphgd->filledPolygon($rootarrow,0x00000000);
	$graphgd->stringFT(0x00000000,$arialfont,8,0,550,40,'Port is root port for this vlan');

	#Add legend
	#Show block icon (a circle), describe it as "Port is blocking" with a string
	#$image->filledArc($cx,$cy,$width,$height,$start,$end,$color [,$arc_style])
	$graphgd->filledArc(507,55,11,11,0,360,0x00000000);
	$graphgd->stringFT(0x00000000,$arialfont,8,0,550,59,'Port is blocking for this vlan');

	#If there are any MST maps, display them for the region
	my $mstposition = 110;
	foreach my $mstregion (sort keys %mstmap) {
		my $mststring = "MST Region $mstregion (";
		foreach my $mstinstance (sort keys %{$mstmap{$mstregion}}) {
			$mststring .= "[$mstinstance:$mstmap{$mstregion}{$mstinstance}]";
		}
		$mststring .= ")";
		$graphgd->stringFT(0x00000000,$arialfont,8,0,0,$mstposition, $mststring);
		$mstposition +=10;
	}
	
	#Finally, display the image
	$graphgd->copyResized($ingraphgd,0,$mstposition,0,0,$inwidth,$inheight,$inwidth,$inheight);
        print header(-type=>'image/png',-expires=>'now',-cache_control=>'no-cache, no-store, must-revalidate');
	print $graphgd->png;
	exit;

}

sub rangify {
    # each @range element is a [ $lower, $upper ] array ref.
    my @ranges;

    return (['all','all']) if ($_[0] eq 'all');

    VAL: for my $val (sort { $a <=> $b } @_) {

        for my $range (@ranges) {
            # is the value already in the range?
            next VAL if $val >= $range->[0] && $val <= $range->[1];

            # extend the range downwards or upwards?
            if ($val == $range->[0] - 1) {
                $range->[0]--;
                next VAL;
            } elsif ($val == $range->[1] + 1) {
                $range->[1]++;
                next VAL;
            }
        }

        # still here? make a new range
        push @ranges, [ $val, $val ];
    }

    my @range_obj = map { intrange(@$_) } @ranges;
    wantarray ? @range_obj : \@range_obj;
}

sub intrange {

    my $lower = shift;
    my $upper = shift;
    my $result;
    my $set;

    if ($lower <= $upper)
    {
        $set = Bit::Vector->new($upper-$lower+1);
        if ((defined $set) && ref($set) && (${$set} != 0))
        {
            $result = [ $set, $lower, $upper ];
            return ($result);
        }
        else
        {
            die ("intrange(): unable to create new 'Set::IntRange' result");
        }
    }
    else
    {
        die ("intrange(): lower > upper boundary");
    }
}
__END__;

