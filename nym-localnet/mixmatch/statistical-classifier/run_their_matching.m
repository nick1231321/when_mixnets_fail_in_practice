function run_their_matching(database_path, average_mixnet_delay)
% Runs MixMatch's statistical flow-matcher on our exported localnet data, using their
% UNMODIFIED align_sequences.m and gaussian_delay_sequences_match_loglikelihood.m and the exact
% merge -> sort -> align -> gaussian-loglikelihood scoring loop from
% perform_experiment_real_data_alt_delay_characteristic.m.
%
% The matching algorithm is entirely theirs; this driver only feeds our exported localnet data
% through it and reports the metric. Differences from their perform_experiment_*.m, all
% non-algorithmic:
%   * train = test = all sessions: on a small localnet run we fit mu/sigma on the true pairs and
%     score all-vs-all, rather than using their separate train/val/test partition files;
%   * average_mixnet_delay is an argument (their one network-specific constant) instead of hardcoded
%     for their topology -- set it to 3 x mixing_ms in seconds (e.g. 0.15 for 50 ms/hop);
%   * TPR@FPR and balanced accuracy are computed here from the score matrix instead of saved to .oct.
%
% database_path:        folder with session_* subfolders + flowpairs.txt (from export_their_format.py)
% average_mixnet_delay: alignment seed / delay lower bound, seconds

  time_units = 1e-9;
  this_dir = fileparts(mfilename('fullpath'));
  addpath(this_dir);                  % our normpdf.m shim (lives next to this driver)
  addpath([this_dir '/upstream']);    % their unmodified align_sequences.m + gaussian_delay_...m

  fid = fopen([database_path '/flowpairs.txt'], 'rt');
  folders = {};
  while ~feof(fid)
    line = fgetl(fid);
    if ischar(line) && ~isempty(line)
      folders{end+1} = line;
    endif
  endwhile
  fclose(fid);
  n = length(folders);
  printf('sessions: %d; average_mixnet_delay = %.1f ms\n', n, 1e3*average_mixnet_delay);

  % load the six timestamp files per session (their column layout)
  for i = 1:n
    f = [database_path '/' folders{i}];
    di_from{i} = load([f '/payload_initiator_from_gateway.txt']) * time_units;
    di_to{i}   = load([f '/payload_initiator_to_gateway.txt'])   * time_units;
    dr_from{i} = load([f '/payload_responder_from_gateway.txt']) * time_units;
    dr_to{i}   = load([f '/payload_responder_to_gateway.txt'])   * time_units;
    ai_from{i} = load([f '/ack_initiator_from_gateway.txt'])     * time_units;
    ar_from{i} = load([f '/ack_responder_from_gateway.txt'])     * time_units;
  endfor

  % --- estimate delay mean/std from the true pairs (their train loop, verbatim structure) ---
  average_delay = 0; average_squared_delay = 0; total_samples = 0;
  for i = 1:n
    data_tx = sort([di_to{i}; dr_to{i}], 'ascend');
    data_rx = sort([di_from{i}; dr_from{i}], 'ascend');
    ack_tx  = data_rx;
    ack_rx  = sort([ai_from{i}; ar_from{i}], 'ascend');
    [adt, adr] = align_sequences(data_tx, data_rx, average_mixnet_delay);
    [aat, aar] = align_sequences(ack_tx,  ack_rx,  average_mixnet_delay);
    average_delay = average_delay + sum(adr - adt) + sum(aar - aat);
    average_squared_delay = average_squared_delay + sum((adr-adt).^2) + sum((aar-aat).^2);
    total_samples = total_samples + length(adr) + length(aar);
  endfor
  average_delay = average_delay / total_samples;
  average_squared_delay = average_squared_delay / total_samples;
  std_dev_delay = sqrt(average_squared_delay - average_delay^2);
  printf('estimated mu = %.2f ms, sigma = %.2f ms (from %d aligned samples)\n', ...
         1e3*average_delay, 1e3*std_dev_delay, total_samples);

  lengths_vector = [100, 500];
  nL = length(lengths_vector) + 1;         % + full
  score = -Inf(n, n, nL);
  min_len_seen = Inf;

  % --- all-vs-all scoring (their test loop, verbatim structure) ---
  for ci = 1:n              % client = initiator of session ci
    for sj = 1:n            % server = responder of session sj
      data_tx = sort([di_to{ci};  dr_to{sj}],  'ascend');
      data_rx = sort([di_from{ci}; dr_from{sj}], 'ascend');
      ack_tx  = data_rx;
      ack_rx  = sort([ai_from{ci}; ar_from{sj}], 'ascend');
      [adt, adr] = align_sequences(data_tx, data_rx, average_mixnet_delay);
      [aat, aar] = align_sequences(ack_tx,  ack_rx,  average_mixnet_delay);
      min_length = min([length(adt), length(aat)]);
      min_len_seen = min(min_len_seen, min_length);
      if min_length == 0, continue; endif
      sv = gaussian_delay_sequences_match_loglikelihood(adt(1:min_length), adr(1:min_length), average_delay, std_dev_delay, true) + ...
           gaussian_delay_sequences_match_loglikelihood(aat(1:min_length), aar(1:min_length), average_delay, std_dev_delay, true);
      for k = 1:length(lengths_vector)
        L = lengths_vector(k);
        if min_length >= L
          score(ci, sj, k) = mean(sv(1:L));
        endif
      endfor
      score(ci, sj, nL) = mean(sv);   % full
    endfor
  endfor
  printf('min aligned length across all pairs: %d\n', min_len_seen);

  labels = {'100', '500', 'full'};
  for k = 1:nL
    S = score(:,:,k);
    report_metrics(S, labels{k});
  endfor
endfunction

function report_metrics(S, label)
  n = size(S, 1);
  true_scores = diag(S);
  mask = ~eye(n);
  false_scores = S(mask);
  true_scores = true_scores(isfinite(true_scores));
  false_scores = false_scores(isfinite(false_scores));
  if numel(false_scores) < 5 || isempty(true_scores)
    printf('  length %-4s : insufficient finite scores (true=%d false=%d)\n', label, numel(true_scores), numel(false_scores));
    return;
  endif
  thr = percentile_(false_scores, 99);           % FPR = 1e-2
  tpr = mean(true_scores > thr);
  % balanced accuracy at the best threshold (1 - min over t of (FAR+FRR)/2)
  allv = [true_scores(:); false_scores(:)];
  ts = linspace(min(allv), max(allv), 300);
  best = 1.0;
  for t = ts
    far = mean(false_scores > t);
    frr = mean(true_scores <= t);
    best = min(best, (far + frr)/2);
  endfor
  printf('  length %-4s : TPR@FPR=1e-2 = %.3f ;  balanced_accuracy = %.3f  (true=%d false=%d)\n', ...
         label, tpr, 1 - best, numel(true_scores), numel(false_scores));
endfunction

function p = percentile_(v, q)
  v = sort(v(:));
  if numel(v) == 1, p = v(1); return; endif
  r = (q/100) * (numel(v) - 1) + 1;            % linear interpolation (numpy default)
  lo = floor(r); hi = ceil(r);
  if lo == hi
    p = v(lo);
  else
    p = v(lo) + (r - lo) * (v(hi) - v(lo));
  endif
endfunction
